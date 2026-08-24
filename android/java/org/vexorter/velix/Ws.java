package org.vexorter.velix;

import android.util.Base64;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.Charset;
import java.security.SecureRandom;
import java.util.Locale;

import javax.net.ssl.SSLSocketFactory;

/**
 * Маленький клиент WebSocket поверх обычного сокета.
 *
 * Готовой библиотеки в Android нет, а тянуть OkHttp ради одного соединения
 * значит тянуть и Gradle со всей его роднёй. Здесь ровно то, что нужно чату:
 * рукопожатие, текстовые и двоичные кадры, ping-pong и закрытие.
 *
 * Кадры от клиента к серверу обязаны быть замаскированы — этого требует
 * RFC 6455, и без маски сервер рвёт соединение.
 */
class Ws {

    interface Listener {
        void onOpen();
        void onText(String text);
        void onBinary(byte[] data);
        void onClosed(String reason);
    }

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final SecureRandom RANDOM = new SecureRandom();

    private final Listener listener;
    private Socket socket;
    private OutputStream output;
    private volatile boolean closed;

    Ws(Listener listener) {
        this.listener = listener;
    }

    /** Открывает соединение и читает кадры, пока оно живо. Блокирует поток. */
    void run(String host, int port, boolean secure, String path) {
        try {
            socket = secure
                    ? SSLSocketFactory.getDefault().createSocket(host, port)
                    : new Socket(host, port);
            socket.setSoTimeout(0);
            output = socket.getOutputStream();
            DataInputStream input = new DataInputStream(socket.getInputStream());

            handshake(host, port, path, input);
            listener.onOpen();
            read(input);
        } catch (Exception error) {
            finish(String.valueOf(error.getMessage()));
        }
    }

    // ------------------------------------------------------- рукопожатие

    private void handshake(String host, int port, String path, DataInputStream input)
            throws IOException {
        byte[] nonce = new byte[16];
        RANDOM.nextBytes(nonce);
        String key = Base64.encodeToString(nonce, Base64.NO_WRAP);

        String request = "GET " + path + " HTTP/1.1\r\n"
                + "Host: " + host + ":" + port + "\r\n"
                + "Upgrade: websocket\r\n"
                + "Connection: Upgrade\r\n"
                + "Sec-WebSocket-Key: " + key + "\r\n"
                + "Sec-WebSocket-Version: 13\r\n\r\n";
        output.write(request.getBytes(UTF8));
        output.flush();

        String status = line(input);
        if (status == null || !status.toLowerCase(Locale.ROOT).contains(" 101")) {
            throw new IOException("сервер ответил: " + status);
        }
        String header;
        while ((header = line(input)) != null && !header.isEmpty()) {
            // Остальные заголовки нам не нужны, но вычитать их обязательно
        }
    }

    private String line(DataInputStream input) throws IOException {
        StringBuilder text = new StringBuilder();
        int previous = 0;
        while (true) {
            int value = input.read();
            if (value < 0) {
                return null;
            }
            if (previous == '\r' && value == '\n') {
                text.setLength(Math.max(text.length() - 1, 0));
                return text.toString();
            }
            text.append((char) value);
            previous = value;
        }
    }

    // ------------------------------------------------------------- чтение

    private void read(DataInputStream input) throws IOException {
        byte[] carry = null;
        int carryOpcode = 0;

        while (!closed) {
            int first = input.read();
            if (first < 0) {
                break;
            }
            boolean fin = (first & 0x80) != 0;
            int opcode = first & 0x0F;

            int second = input.read();
            if (second < 0) {
                break;
            }
            boolean masked = (second & 0x80) != 0;
            long length = second & 0x7F;
            if (length == 126) {
                length = ((long) input.read() << 8) | input.read();
            } else if (length == 127) {
                length = 0;
                for (int index = 0; index < 8; index++) {
                    length = (length << 8) | input.read();
                }
            }

            byte[] mask = new byte[4];
            if (masked) {
                input.readFully(mask);
            }

            byte[] payload = new byte[(int) length];
            input.readFully(payload);
            if (masked) {
                for (int index = 0; index < payload.length; index++) {
                    payload[index] ^= mask[index % 4];
                }
            }

            if (opcode == 0x8) {           // закрытие
                break;
            }
            if (opcode == 0x9) {           // ping — отвечаем pong
                send(0xA, payload);
                continue;
            }
            if (opcode == 0xA) {           // pong, ответа не требует
                continue;
            }

            // Длинное сообщение сервер может прислать кусками
            if (opcode == 0x0) {
                carry = join(carry, payload);
            } else if (!fin) {
                carry = payload;
                carryOpcode = opcode;
                continue;
            } else {
                carry = payload;
                carryOpcode = opcode;
            }

            if (fin) {
                if (carryOpcode == 0x1) {
                    listener.onText(new String(carry, UTF8));
                } else if (carryOpcode == 0x2) {
                    listener.onBinary(carry);
                }
                carry = null;
            }
        }
        finish("соединение закрыто");
    }

    private static byte[] join(byte[] first, byte[] second) {
        if (first == null) {
            return second;
        }
        byte[] both = new byte[first.length + second.length];
        System.arraycopy(first, 0, both, 0, first.length);
        System.arraycopy(second, 0, both, first.length, second.length);
        return both;
    }

    // ------------------------------------------------------------ запись

    void sendText(String text) {
        send(0x1, text.getBytes(UTF8));
    }

    void sendBinary(byte[] data) {
        send(0x2, data);
    }

    private synchronized void send(int opcode, byte[] payload) {
        if (closed || output == null) {
            return;
        }
        try {
            output.write(0x80 | opcode);

            int length = payload.length;
            if (length < 126) {
                output.write(0x80 | length);
            } else if (length < 65536) {
                output.write(0x80 | 126);
                output.write((length >> 8) & 0xFF);
                output.write(length & 0xFF);
            } else {
                output.write(0x80 | 127);
                for (int shift = 56; shift >= 0; shift -= 8) {
                    output.write((int) (((long) length >> shift) & 0xFF));
                }
            }

            byte[] mask = new byte[4];
            RANDOM.nextBytes(mask);
            output.write(mask);

            byte[] masked = new byte[length];
            for (int index = 0; index < length; index++) {
                masked[index] = (byte) (payload[index] ^ mask[index % 4]);
            }
            output.write(masked);
            output.flush();
        } catch (IOException error) {
            finish(String.valueOf(error.getMessage()));
        }
    }

    void close() {
        if (!closed) {
            send(0x8, new byte[0]);
        }
        finish("закрыли сами");
    }

    private void finish(String reason) {
        if (closed) {
            return;
        }
        closed = true;
        try {
            if (socket != null) {
                socket.close();
            }
        } catch (IOException ignored) {
            // Уже закрыт — и хорошо
        }
        listener.onClosed(reason);
    }

    boolean isClosed() {
        return closed;
    }
}
