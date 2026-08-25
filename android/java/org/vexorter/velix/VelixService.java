package org.vexorter.velix;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;

/**
 * Соединение с сервером живёт здесь, а не на экране.
 *
 * Так решаются сразу две беды: приложение не переподключается всякий раз,
 * когда экран пересоздаётся, и приходят уведомления — служба продолжает
 * слушать сокет, пока чат свёрнут.
 *
 * Android держит фоновую работу на коротком поводке, поэтому служба
 * объявлена «постоянной» и показывает свою строчку в шторке: без неё
 * система усыпила бы соединение через несколько минут.
 */
public class VelixService extends Service implements Net.Listener {

    static final String CHANNEL_LIVE = "velix-live";
    static final String CHANNEL_MESSAGES = "velix-messages";
    private static final int LIVE_ID = 1;

    /** Экран, пока он виден. Когда его нет, сообщения уходят в уведомления. */
    interface Screen {
        void onOpen(boolean secure);
        void onFrame(JSONObject frame);
        void onBlob(JSONObject header, byte[] data);
        void onClosed(String reason);
    }

    class Local extends Binder {
        VelixService service() {
            return VelixService.this;
        }
    }

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Local binder = new Local();
    private final Map<Integer, Integer> unread = new HashMap<>();

    private Net net;
    private Screen screen;
    private JSONObject welcome, conversations, people;
    private int openConversation = -1;
    private int attempt;
    private boolean stopping;

    @Override
    public void onCreate() {
        super.onCreate();
        channels();
        startForeground(LIVE_ID, live());
        connect();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        return START_STICKY;   // система перезапустит нас, если убьёт
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    private void channels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(
                CHANNEL_LIVE, Lang.t("Velix на связи"),
                NotificationManager.IMPORTANCE_MIN));
        manager.createNotificationChannel(new NotificationChannel(
                CHANNEL_MESSAGES, Lang.t("Сообщения"),
                NotificationManager.IMPORTANCE_HIGH));
    }

    private PendingIntent openApp() {
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
        return PendingIntent.getActivity(this, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private Notification live() {
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_LIVE)
                : new Notification.Builder(this);
        return builder
                .setContentTitle("Velix")
                .setContentText(Lang.t("Velix на связи"))
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentIntent(openApp())
                .setOngoing(true)
                .build();
    }

    private void notifyMessage(String who, String what, int conversation) {
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_MESSAGES)
                : new Notification.Builder(this);
        Notification notification = builder
                .setContentTitle(who)
                .setContentText(what)
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentIntent(openApp())
                .setAutoCancel(true)
                .build();
        getSystemService(NotificationManager.class)
                .notify(1000 + conversation, notification);
    }

    // ------------------------------------------------------------- связь

    private void connect() {
        String server = getSharedPreferences("velix", MODE_PRIVATE)
                .getString("server", "");
        if (server.isEmpty()) {
            return;
        }
        net = new Net(this);
        net.connect(server);
    }

    void send(JSONObject frame) {
        if (net != null) {
            net.send(frame);
        }
    }

    void send(JSONObject frame, byte[] payload) {
        if (net != null) {
            net.send(frame, payload);
        }
    }

    /** Экран появился: отдаём ему то, что уже знаем, и просим свежее. */
    void attach(Screen listener) {
        screen = listener;
        if (welcome != null) {
            // Помечаем: это не новый вход, а напоминание о том, кто мы.
            // Иначе экран принял бы его за свежий и ушёл бы в список.
            try {
                JSONObject again = new JSONObject(welcome.toString());
                again.put("cached", true);
                listener.onFrame(again);
            } catch (Exception error) {
                listener.onFrame(welcome);
            }
        }
        if (conversations != null) {
            listener.onFrame(conversations);
        }
        if (people != null) {
            listener.onFrame(people);
        }
        send(Net.frame("sync"));
    }

    int openConversation() {
        return openConversation;
    }

    void rememberOpen(int conversation) {
        openConversation = conversation;
    }

    void detach() {
        screen = null;
    }

    boolean connected() {
        return welcome != null;
    }

    int unreadFor(int conversation) {
        Integer count = unread.get(conversation);
        return count == null ? 0 : count;
    }

    Map<Integer, Integer> unreadAll() {
        return unread;
    }

    void clearUnread(int conversation) {
        unread.remove(conversation);
        getSystemService(NotificationManager.class).cancel(1000 + conversation);
    }

    /** Полный выход: соединение закрываем и службу снимаем. */
    void shutdown() {
        stopping = true;
        welcome = null;
        if (net != null) {
            net.close();
        }
        stopForeground(true);
        stopSelf();
    }

    // ------------------------------------------------------ кадры сервера

    @Override
    public void onOpen(boolean secure) {
        attempt = 0;
        String token = getSharedPreferences("velix", MODE_PRIVATE)
                .getString("token", "");
        if (!token.isEmpty()) {
            send(Net.frame("auth", "token", token));
        }
        if (screen != null) {
            screen.onOpen(secure);
        }
    }

    @Override
    public void onFrame(JSONObject frame) {
        String kind = frame.optString("type");
        if ("welcome".equals(kind)) {
            // Код восстановления показывают один раз: в запас его не кладём,
            // иначе он всплывал бы при каждом пересоздании экрана
            welcome = frame;
            try {
                JSONObject kept = new JSONObject(frame.toString());
                kept.remove("recovery");
                welcome = kept;
            } catch (Exception ignored) {
                // Оставим как есть — хуже от этого не будет
            }
            if (screen != null) {
                screen.onFrame(frame);
                return;
            }
        } else if ("conversations".equals(kind)) {
            conversations = frame;
        } else if ("people".equals(kind)) {
            people = frame;
        } else if ("conversation".equals(kind)) {
            // Список в запасе тоже должен знать о новой переписке
            mergeConversation(frame.optJSONObject("item"));
        }

        // Пока чат свёрнут, о новом сообщении сообщает система
        if (screen == null && ("text".equals(kind) || "media".equals(kind))) {
            int conversation = frame.optInt("conversation");
            Integer was = unread.get(conversation);
            unread.put(conversation, (was == null ? 0 : was) + 1);

            String what = "text".equals(kind)
                    ? frame.optString("text") : Lang.t("вложение");
            notifyMessage(frame.optString("nick"), what, conversation);
        }

        if (screen != null) {
            screen.onFrame(frame);
        }
    }

    private void mergeConversation(JSONObject item) {
        if (item == null || conversations == null) {
            return;
        }
        try {
            org.json.JSONArray items = conversations.optJSONArray("items");
            if (items == null) {
                return;
            }
            org.json.JSONArray fresh = new org.json.JSONArray();
            for (int index = 0; index < items.length(); index++) {
                JSONObject known = items.optJSONObject(index);
                if (known != null && known.optInt("id") != item.optInt("id")) {
                    fresh.put(known);
                }
            }
            fresh.put(item);
            conversations.put("items", fresh);
        } catch (Exception ignored) {
            // Не сложилось — экран всё равно попросит свежий список
        }
    }

    @Override
    public void onBlob(JSONObject header, byte[] data) {
        if (screen != null) {
            screen.onBlob(header, data);
        }
    }

    @Override
    public void onClosed(String reason) {
        if (screen != null) {
            screen.onClosed(reason);
        }
        if (stopping) {
            return;
        }

        // Переподключаемся тихо и с растущей паузой: связь на телефоне
        // пропадает часто, и дёргать сервер каждую секунду незачем
        attempt = Math.min(attempt + 1, 6);
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (!stopping) {
                    connect();
                }
            }
        }, Math.min(1000L * (1L << attempt), 30000L));
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        stopping = true;
        if (net != null) {
            net.close();
        }
    }
}
