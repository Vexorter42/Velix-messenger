package org.vexorter.velix;

import android.os.Handler;
import android.os.Looper;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Разговор с сервером Velix: тот же протокол, что у оконного клиента.
 *
 * Кадр — это JSON; за описанием вложения сразу идёт двоичный кадр с
 * содержимым. Сокет живёт в отдельном потоке, а до экрана события доходят
 * через Handler: трогать виджеты из чужого потока в Android нельзя.
 */
class Net implements Ws.Listener {

    interface Listener {
        void onOpen(boolean secure);
        void onFrame(JSONObject frame);
        void onBlob(JSONObject header, byte[] data);
        void onClosed(String reason);
    }

    static final int VERSION = 5;
    static final int MAX_MEDIA = 25 * 1024 * 1024;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Listener listener;
    private Ws socket;
    private JSONObject pendingHeader;   // описание вложения ждёт свои байты
    private volatile boolean secure;

    Net(Listener listener) {
        this.listener = listener;
    }

    /**
     * Подключается к серверу. Сначала пробуем защищённое соединение и только
     * потом открытое — как в оконном клиенте.
     */
    void connect(final String address) {
        final String host = host(address);
        final int port = port(address);
        final boolean forcePlain = address.startsWith("ws://")
                || address.startsWith("http://");

        new Thread(new Runnable() {
            @Override
            public void run() {
                if (!forcePlain) {
                    secure = true;
                    socket = new Ws(Net.this);
                    socket.run(host, port, true, "/");
                    if (opened) {
                        return;
                    }
                }
                secure = false;
                socket = new Ws(Net.this);
                socket.run(host, port, false, "/");
            }
        }, "velix-net").start();
    }

    private volatile boolean opened;

    static String host(String address) {
        String value = strip(address);
        int colon = value.lastIndexOf(':');
        return colon > 0 ? value.substring(0, colon) : value;
    }

    static int port(String address) {
        String value = strip(address);
        int colon = value.lastIndexOf(':');
        if (colon > 0) {
            try {
                return Integer.parseInt(value.substring(colon + 1));
            } catch (NumberFormatException ignored) {
                // Порт не разобрался — берём обычный
            }
        }
        return 8765;
    }

    private static String strip(String address) {
        String value = address.trim();
        for (String scheme : new String[]{"wss://", "ws://", "https://", "http://"}) {
            if (value.startsWith(scheme)) {
                value = value.substring(scheme.length());
            }
        }
        int slash = value.indexOf('/');
        return slash > 0 ? value.substring(0, slash) : value;
    }

    // ------------------------------------------------------------ отправка

    // Писать в сокет из главного потока Android не даёт: любая отправка
    // уходит в свой поток. Он один, поэтому описание вложения и его байты
    // не разъедутся.
    private final ExecutorService writer = Executors.newSingleThreadExecutor();

    void send(final JSONObject frame) {
        writer.execute(new Runnable() {
            @Override
            public void run() {
                if (socket != null && !socket.isClosed()) {
                    socket.sendText(frame.toString());
                }
            }
        });
    }

    void send(final JSONObject frame, final byte[] payload) {
        writer.execute(new Runnable() {
            @Override
            public void run() {
                if (socket == null || socket.isClosed()) {
                    return;
                }
                socket.sendText(frame.toString());
                socket.sendBinary(payload);
            }
        });
    }

    void close() {
        writer.execute(new Runnable() {
            @Override
            public void run() {
                if (socket != null) {
                    socket.close();
                }
            }
        });
    }

    boolean isSecure() {
        return secure;
    }

    /** Собирает кадр: тип и пары ключ-значение подряд. */
    static JSONObject frame(String type, Object... pairs) {
        try {
            JSONObject json = new JSONObject();
            json.put("v", VERSION);
            json.put("type", type);
            for (int index = 0; index + 1 < pairs.length; index += 2) {
                json.put(String.valueOf(pairs[index]), pairs[index + 1]);
            }
            return json;
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    static JSONArray numbers(Iterable<Integer> values) {
        JSONArray array = new JSONArray();
        for (Integer value : values) {
            array.put((int) value);
        }
        return array;
    }

    // ------------------------------------------------------- события сокета

    @Override
    public void onOpen() {
        opened = true;
        main.post(new Runnable() {
            @Override
            public void run() {
                listener.onOpen(secure);
            }
        });
    }

    @Override
    public void onText(String text) {
        try {
            final JSONObject frame = new JSONObject(text);
            String kind = frame.optString("type");
            if ("blob".equals(kind) || "update_blob".equals(kind)) {
                pendingHeader = frame;      // содержимое придёт следующим кадром
                return;
            }
            main.post(new Runnable() {
                @Override
                public void run() {
                    listener.onFrame(frame);
                }
            });
        } catch (Exception ignored) {
            // Не разобрали кадр — пропускаем, связь от этого не рвётся
        }
    }

    @Override
    public void onBinary(final byte[] data) {
        final JSONObject header = pendingHeader;
        pendingHeader = null;
        if (header == null) {
            return;
        }
        main.post(new Runnable() {
            @Override
            public void run() {
                listener.onBlob(header, data);
            }
        });
    }

    @Override
    public void onClosed(final String reason) {
        if (!opened && secure) {
            return;   // защищённая попытка не удалась, сейчас будет обычная
        }
        opened = false;
        main.post(new Runnable() {
            @Override
            public void run() {
                listener.onClosed(reason);
            }
        });
    }
}
