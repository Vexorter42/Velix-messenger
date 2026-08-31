package org.vexorter.velix;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.ComponentName;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Outline;
import android.hardware.Camera;
import android.media.CamcorderProfile;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.text.InputType;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.view.WindowManager;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.VideoView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Velix для Android — настоящий клиент, а не окно с веб-страницей.
 *
 * Экраны собраны кодом и переключаются в одном окне: соединение с сервером
 * живёт всё это время одно, и при переходе из списка в переписку ничего не
 * переподключается.
 */
public class MainActivity extends Activity implements VelixService.Screen {

    private static final String PREFS = "velix";
    private static final int PICK_PHOTO = 1;
    private static final int PICK_FILE = 3;

    // Пределы вложений называет сервер в приветствии
    private JSONObject limits = new JSONObject();
    private final Map<String, Object[]> pendingUploads = new HashMap<>();
    private TextView uploadLine;
    private Uri pendingUpload;      // файл, выбранный до готовности связи
    private View settingsScreen;
    private EditText searchField;
    private TextView peopleTitle;
    private final Handler main = new Handler(Looper.getMainLooper());
    private static final String[] EMOJI = {"👍", "❤", "😂", "🔥", "😢", "👎"};

    private VelixService service;
    private boolean bound;
    private FrameLayout root;
    private View authScreen, listScreen, chatScreen;

    // --- вход
    private EditText serverField, loginField, passwordField, nameField, inviteField;
    private TextView primaryButton, switchButton, authError, authSubtitle;
    private boolean registerMode;
    private boolean recoverMode;
    private EditText codeField;
    private TextView forgotButton;

    // --- списки
    private LinearLayout chatsBox, peopleBox;
    private TextView listHint;

    // --- переписка
    private LinearLayout feed, pinBar;
    private ScrollView feedScroll;
    private TextView chatTitle, chatStatus, pinLabel, replyLabel;
    private View replyBar;
    private EditText messageField;
    private TextView chatAvatar;

    // --- состояние
    private JSONObject me = new JSONObject();
    private final List<JSONObject> conversations = new ArrayList<>();
    private final List<JSONObject> people = new ArrayList<>();
    private final Set<Integer> online = new HashSet<>();
    private final Map<Integer, String> seen = new HashMap<>();

    /** Вложения этой переписки по порядку — их и листают в полном экране. */
    private final List<JSONObject> gallery = new ArrayList<>();
    private final Map<String, String> videoFiles = new HashMap<>();
    private final Map<String, String> waitingVideos = new HashMap<>();
    private String viewerWaiting = "";
    private boolean drawingHistory = false;
    private Runnable viewerRepaint = null;
    private String typingWho = null;
    private long typingUntil = 0;
    private long typingSent = 0;
    private final List<JSONObject> items = new ArrayList<>();
    private final Map<Integer, TextView> ticks = new HashMap<>();
    private final Map<Integer, String> states = new HashMap<>();
    private final Map<String, JSONObject> pinned = new HashMap<>();
    private final Map<String, byte[]> media = new HashMap<>();
    private final Map<String, List<ImageView>> waiting = new HashMap<>();
    private final Map<Integer, JSONObject> reactions = new HashMap<>();
    private final Map<Integer, LinearLayout> reactionRows = new HashMap<>();
    private final Map<Integer, JSONObject> quotes = new HashMap<>();
    private final Map<String, byte[]> localMedia = new HashMap<>();
    private int conversation = -1;
    private int replyTo = -1;
    private int editing = 0;        // какое своё сообщение правим
    private final Map<Integer, String> drafts = new HashMap<>();
    private final List<JSONObject> outbox = new ArrayList<>();
    private JSONObject apkOffer;    // что за приложение раздаёт сервер
    private boolean fromCache;      // показываем сохранённое, связи ещё нет
    private int localNumber;
    private boolean pendingGroup;
    private int pendingDirect = -1;   // ждём переписку с этим человеком

    // ------------------------------------------------------------- запуск

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Lang.set(prefs().getString("language", "en"));
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);

        root = new FrameLayout(this);
        root.setBackgroundColor(Ui.CHAT_BG);
        setContentView(root);

        authScreen = buildAuth();
        listScreen = buildList();
        chatScreen = buildChat();
        root.addView(authScreen);
        root.addView(listScreen);
        root.addView(chatScreen);
        show(authScreen);

        askNotifications();

        String token = prefs().getString("token", null);
        if (token != null) {
            authSubtitle.setText(Lang.t("Подключаемся…"));
            // Сохранённое показываем сразу: в метро это единственное, что
            // вообще можно показать, а связь подтянется сама
            showSaved();
            startService();
        }
    }

    /** С Android 13 разрешение на уведомления спрашивают отдельно. */
    private void askNotifications() {
        if (Build.VERSION.SDK_INT < 33) {
            return;
        }
        if (checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 2);
        }
    }

    /** Поднимает службу со связью и цепляется к ней. */
    private void startService() {
        Intent intent = new Intent(this, VelixService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        bindService(intent, connection, BIND_AUTO_CREATE);
    }

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = ((VelixService.Local) binder).service();
            bound = true;
            service.attach(MainActivity.this);

            // Экран мог пересоздаться, пока выбирали фото: возвращаемся туда,
            // где человек был
            int open = service.openConversation();
            if (open >= 0 && conversation < 0) {
                openConversation(open);
            }
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            service = null;
            bound = false;
        }
    };

    private void send(JSONObject frame) {
        if (service != null) {
            service.send(frame);
        }
    }

    private void send(JSONObject frame, byte[] payload) {
        if (service != null) {
            service.send(frame, payload);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (service != null) {
            service.attach(this);
            if (conversation >= 0) {
                service.clearUnread(conversation);
                // Пока чат был свёрнут, сюда могли написать: перечитываем
                // ленту, иначе пришедшее увиделось бы только после выхода
                openConversation(conversation);
            }
            drawList();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (service != null) {
            service.detach();
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private void show(View screen) {
        boolean менялся = screen.getVisibility() != View.VISIBLE;
        authScreen.setVisibility(screen == authScreen ? View.VISIBLE : View.GONE);
        listScreen.setVisibility(screen == listScreen ? View.VISIBLE : View.GONE);
        chatScreen.setVisibility(screen == chatScreen ? View.VISIBLE : View.GONE);

        if (!менялся) {
            return;
        }
        // Экран не выпрыгивает, а проявляется с лёгким сдвигом снизу
        screen.setAlpha(0f);
        screen.setTranslationY(Ui.dp(this, 10));
        screen.animate().alpha(1f).translationY(0f).setDuration(180).start();
    }

    // -------------------------------------------------------- экран входа

    private View buildAuth() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout card = Ui.column(this);
        card.setPadding(Ui.dp(this, 24), Ui.dp(this, 60), Ui.dp(this, 24),
                Ui.dp(this, 24));
        scroll.addView(card);

        TextView logo = Ui.avatar(this, "Velix", Ui.dp(this, 78));
        logo.setText("V");
        logo.setBackground(Ui.circle(Ui.ACCENT));
        LinearLayout.LayoutParams logoParams =
                new LinearLayout.LayoutParams(Ui.dp(this, 78), Ui.dp(this, 78));
        logoParams.gravity = Gravity.CENTER_HORIZONTAL;
        logo.setLayoutParams(logoParams);
        card.addView(logo);

        TextView title = Ui.text(this, "Velix", 26, Ui.TEXT);
        title.setGravity(Gravity.CENTER);
        card.addView(title, Ui.wide());

        authSubtitle = Ui.text(this, Lang.t("Вход в аккаунт"), 14, Ui.MUTED);
        authSubtitle.setGravity(Gravity.CENTER);
        card.addView(authSubtitle, Ui.wide());

        serverField = Ui.field(this, Lang.t("Адрес сервера"));
        serverField.setText(prefs().getString("server", "velix.vexorter.duckdns.org:8765"));
        loginField = Ui.field(this, Lang.t("Логин"));
        loginField.setInputType(InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        passwordField = Ui.field(this, Lang.t("Пароль"));
        passwordField.setInputType(InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        nameField = Ui.field(this, Lang.t("Как вас зовут"));
        inviteField = Ui.field(this, Lang.t("Код приглашения"));
        codeField = Ui.field(this, Lang.t("Код восстановления"));

        for (EditText field : new EditText[]{serverField, loginField, codeField,
                                             passwordField, nameField, inviteField}) {
            card.addView(field, Ui.wide());
            Ui.margins(field, 0, Ui.dp(this, 8), 0, 0);
        }
        nameField.setVisibility(View.GONE);
        inviteField.setVisibility(View.GONE);
        codeField.setVisibility(View.GONE);

        primaryButton = Ui.button(this, Lang.t("Войти"), Ui.ACCENT, Ui.TEXT);
        primaryButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                signIn();
            }
        });
        card.addView(primaryButton, Ui.wide());
        Ui.margins(primaryButton, 0, Ui.dp(this, 16), 0, 0);

        switchButton = Ui.button(this, Lang.t("Создать аккаунт"), Color.TRANSPARENT,
                Ui.ACCENT);
        switchButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                if (recoverMode) {
                    recoverMode = false;
                } else {
                    registerMode = !registerMode;
                }
                drawAuthMode();
            }
        });
        card.addView(switchButton, Ui.wide());

        forgotButton = Ui.button(this, Lang.t("Забыли пароль?"), Color.TRANSPARENT,
                Ui.MUTED);
        forgotButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                recoverMode = true;
                registerMode = false;
                drawAuthMode();
            }
        });
        card.addView(forgotButton, Ui.wide());

        authError = Ui.text(this, "", 14, Ui.DANGER);
        authError.setGravity(Gravity.CENTER);
        card.addView(authError, Ui.wide());
        Ui.margins(authError, 0, Ui.dp(this, 12), 0, 0);

        return scroll;
    }

    /** Приводит экран входа к выбранному режиму. */
    private void drawAuthMode() {
        nameField.setVisibility(registerMode ? View.VISIBLE : View.GONE);
        inviteField.setVisibility(registerMode ? View.VISIBLE : View.GONE);
        codeField.setVisibility(recoverMode ? View.VISIBLE : View.GONE);
        forgotButton.setVisibility(registerMode || recoverMode
                ? View.GONE : View.VISIBLE);
        passwordField.setHint(recoverMode ? Lang.t("Новый пароль")
                                          : Lang.t("Пароль"));

        if (recoverMode) {
            primaryButton.setText(Lang.t("Сменить пароль"));
            switchButton.setText(Lang.t("Вернуться ко входу"));
            authSubtitle.setText(Lang.t("Восстановление пароля"));
        } else {
            primaryButton.setText(registerMode ? Lang.t("Создать аккаунт")
                                               : Lang.t("Войти"));
            switchButton.setText(registerMode ? Lang.t("У меня уже есть аккаунт")
                                              : Lang.t("Создать аккаунт"));
            authSubtitle.setText(registerMode ? Lang.t("Нужен код приглашения")
                                              : Lang.t("Вход в аккаунт"));
        }
        authError.setText("");
    }

    /** Показывает код восстановления — единственный раз, когда он виден. */
    private void showRecovery(final String code) {
        LinearLayout card = Ui.column(this);
        card.setPadding(Ui.dp(this, 24), Ui.dp(this, 12), Ui.dp(this, 24), 0);

        TextView value = Ui.text(this, code, 22, Ui.ACCENT);
        value.setGravity(Gravity.CENTER);
        card.addView(value, Ui.wide());

        card.addView(Ui.text(this,
                Lang.t("По нему меняют пароль, если его забыли. Другого способа нет: "
                        + "почту мы не спрашиваем, а сервер стоит у вас дома."),
                14, Ui.MUTED), Ui.wide());

        new AlertDialog.Builder(this)
                .setTitle(Lang.t("Сохраните код восстановления"))
                .setView(card)
                .setCancelable(false)
                .setPositiveButton(Lang.t("Понятно"), null)
                .setNeutralButton(Lang.t("Копировать"),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                ClipboardManager clipboard = (ClipboardManager)
                                        getSystemService(Context.CLIPBOARD_SERVICE);
                                clipboard.setPrimaryClip(
                                        ClipData.newPlainText("velix", code));
                                toast(Lang.t("Код скопирован"));
                            }
                        })
                .show();
    }

    private void signIn() {
        String login = loginField.getText().toString().trim();
        String password = passwordField.getText().toString();
        if (login.isEmpty() || password.isEmpty()
                || (recoverMode && codeField.getText().toString().trim().isEmpty())) {
            authError.setText(recoverMode
                    ? Lang.t("Заполните логин, код и новый пароль.")
                    : Lang.t("Заполните логин и пароль."));
            return;
        }

        prefs().edit().putString("server", serverField.getText().toString().trim())
                .putString("login", login).apply();
        authError.setText("");
        authSubtitle.setText(Lang.t("Подключаемся…"));
        pendingSignIn = true;
        if (service != null) {
            service.restart();      // служба уже привязана — просто заново
        } else {
            startService();
        }
    }

    // Вход руками: как только служба откроет сокет, отправим логин или код
    private boolean pendingSignIn;

    // ----------------------------------------------------- экран со списком

    private View buildList() {
        LinearLayout screen = Ui.column(this);
        screen.setBackgroundColor(Ui.CHAT_BG);

        LinearLayout bar = Ui.row(this);
        bar.setBackgroundColor(Ui.SIDEBAR);
        bar.setPadding(Ui.dp(this, 16), Ui.dp(this, 14), Ui.dp(this, 16),
                Ui.dp(this, 14));
        TextView title = Ui.text(this, "Velix", 20, Ui.TEXT);
        bar.addView(title, Ui.grow());

        TextView profile = Ui.text(this, "⋯", 22, Ui.MUTED);
        profile.setPadding(Ui.dp(this, 12), 0, 0, 0);
        profile.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                openSettings();
            }
        });
        bar.addView(profile);
        screen.addView(bar, Ui.wide());

        searchField = Ui.field(this, Lang.t("Поиск: @username или слово"));
        searchField.setSingleLine(true);
        searchField.addTextChangedListener(new android.text.TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {
                drawList();
            }

            @Override
            public void afterTextChanged(android.text.Editable s) {
            }
        });
        LinearLayout поисковая = Ui.row(this);
        поисковая.setPadding(Ui.dp(this, 12), 0, Ui.dp(this, 12), 0);
        поисковая.addView(searchField, Ui.grow());
        screen.addView(поисковая, Ui.wide());

        TextView newGroup = Ui.button(this, Lang.t("Новая группа"), Color.TRANSPARENT,
                Ui.ACCENT);
        newGroup.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                newGroup();
            }
        });
        screen.addView(newGroup, Ui.wide());

        ScrollView scroll = new ScrollView(this);
        LinearLayout column = Ui.column(this);
        scroll.addView(column);

        listHint = Ui.text(this,
                Lang.t("Создайте группу или найдите человека по @username."),
                14, Ui.MUTED);
        listHint.setPadding(Ui.dp(this, 16), Ui.dp(this, 8), Ui.dp(this, 16),
                Ui.dp(this, 8));
        column.addView(listHint, Ui.wide());

        chatsBox = Ui.column(this);
        column.addView(chatsBox, Ui.wide());

        peopleTitle = Ui.text(this, Lang.t("ЛЮДИ"), 12, Ui.MUTED);
        peopleTitle.setPadding(Ui.dp(this, 16), Ui.dp(this, 16), Ui.dp(this, 16),
                Ui.dp(this, 6));
        peopleTitle.setVisibility(View.GONE);
        column.addView(peopleTitle, Ui.wide());

        peopleBox = Ui.column(this);
        column.addView(peopleBox, Ui.wide());

        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0);
        params.weight = 1;
        screen.addView(scroll, params);
        return screen;
    }

    private void drawList() {
        chatsBox.removeAllViews();
        listHint.setVisibility(conversations.isEmpty() ? View.VISIBLE : View.GONE);

        String запрос = searchField == null ? ""
                : searchField.getText().toString().trim()
                        .replace("@", "").toLowerCase();

        for (final JSONObject item : conversations) {
            if (!запрос.isEmpty()
                    && !titleOf(item).toLowerCase().contains(запрос)) {
                continue;
            }
            chatsBox.addView(conversationRow(item), Ui.wide());
        }

        // Всех подряд не показываем: люди находятся поиском по @username
        peopleBox.removeAllViews();
        peopleTitle.setVisibility(запрос.isEmpty() ? View.GONE : View.VISIBLE);
        if (запрос.isEmpty()) {
            return;
        }
        int нашлось = 0;
        for (final JSONObject person : people) {
            if (person.optInt("id") == me.optInt("id")) {
                continue;
            }
            if (!person.optString("login").toLowerCase().contains(запрос)
                    && !person.optString("name").toLowerCase().contains(запрос)) {
                continue;
            }
            peopleBox.addView(personRow(person), Ui.wide());
            нашлось += 1;
        }
        if (нашлось == 0) {
            peopleTitle.setVisibility(View.GONE);
        }
    }

    private View conversationRow(final JSONObject item) {
        LinearLayout row = Ui.row(this);
        row.setPadding(Ui.dp(this, 14), Ui.dp(this, 10), Ui.dp(this, 14),
                Ui.dp(this, 10));
        String title = titleOf(item);
        TextView face = Ui.avatar(this, title, Ui.dp(this, 46));
        row.addView(face);
        paintPhoto(face, item.optString("avatar", ""));

        LinearLayout lines = Ui.column(this);
        lines.setPadding(Ui.dp(this, 12), 0, 0, 0);
        // У группы значок: иначе её не отличить от человека
        lines.addView(Ui.text(this,
                ("group".equals(item.optString("kind")) ? "\uD83D\uDC65 " : "")
                        + title, 16, Ui.TEXT), Ui.wide());

        JSONObject last = item.optJSONObject("last");
        String preview = Lang.t("нет сообщений");
        if (last != null) {
            String what = whatItWas(last);
            preview = last.optString("nick", "") + ": " + what;
        }
        lines.addView(Ui.text(this, cut(preview, 40), 13, Ui.MUTED), Ui.wide());
        row.addView(lines, Ui.grow());

        // Сколько пришло, пока сюда не заглядывали
        int waiting = service == null ? 0 : service.unreadFor(item.optInt("id"));
        if (waiting > 0) {
            TextView badge = Ui.text(this, String.valueOf(waiting), 13, Ui.TEXT);
            badge.setGravity(Gravity.CENTER);
            badge.setBackground(Ui.circle(Ui.DANGER));
            badge.setPadding(Ui.dp(this, 8), Ui.dp(this, 2), Ui.dp(this, 8),
                    Ui.dp(this, 2));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            params.setMargins(Ui.dp(this, 8), 0, 0, 0);
            row.addView(badge, params);
        }

        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                openConversation(item.optInt("id"));
            }
        });
        row.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View view) {
                groupMenu(item);
                return true;
            }
        });
        return row;
    }

    /** Что можно сделать с группой: сменить фото, удалить. */
    private void groupMenu(final JSONObject item) {
        if (!"group".equals(item.optString("kind"))) {
            return;
        }

        final boolean mine = item.optInt("owner", -1) == me.optInt("id");
        List<String> actions = new ArrayList<>();
        actions.add(Lang.t("Фото группы"));
        actions.add(Lang.t("Позвать людей"));
        if (mine) {
            actions.add(Lang.t("Удалить группу"));
        }

        new AlertDialog.Builder(this)
                .setTitle(titleOf(item))
                .setItems(actions.toArray(new String[0]),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                if (which == 0) {
                                    photoTarget = item.optInt("id");
                                    pickPhoto();
                                } else if (which == 1) {
                                    inviteToGroup(item);
                                } else {
                                    confirmDeleteGroup(item);
                                }
                            }
                        })
                .show();
    }

    /** Кого позвать в уже заведённую группу. */
    private void inviteToGroup(final JSONObject item) {
        org.json.JSONArray уже = item.optJSONArray("members");
        final List<JSONObject> свободные = new ArrayList<>();
        for (JSONObject person : people) {
            if (person.optInt("id") == me.optInt("id")) {
                continue;
            }
            boolean внутри = false;
            for (int index = 0; уже != null && index < уже.length(); index++) {
                if (уже.optInt(index) == person.optInt("id")) {
                    внутри = true;
                    break;
                }
            }
            if (!внутри) {
                свободные.add(person);
            }
        }

        if (свободные.isEmpty()) {
            toast(Lang.t("Все уже в группе."));
            return;
        }

        String[] подписи = new String[свободные.size()];
        final boolean[] отмечены = new boolean[свободные.size()];
        for (int index = 0; index < свободные.size(); index++) {
            подписи[index] = свободные.get(index).optString("name")
                    + " · @" + свободные.get(index).optString("login");
        }

        new AlertDialog.Builder(this)
                .setTitle(Lang.t("Позвать людей"))
                .setMultiChoiceItems(подписи, отмечены,
                        new DialogInterface.OnMultiChoiceClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which,
                                                boolean checked) {
                                отмечены[which] = checked;
                            }
                        })
                .setPositiveButton(Lang.t("Позвать"),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                org.json.JSONArray кого = new org.json.JSONArray();
                                for (int index = 0; index < отмечены.length; index++) {
                                    if (отмечены[index]) {
                                        кого.put(свободные.get(index).optInt("id"));
                                    }
                                }
                                if (кого.length() > 0) {
                                    send(Net.frame("members", "conversation",
                                            item.optInt("id"), "members", кого));
                                }
                            }
                        })
                .setNegativeButton(Lang.t("Отмена"), null)
                .show();
    }

    private void confirmDeleteGroup(final JSONObject item) {
        new AlertDialog.Builder(this)
                .setTitle(Lang.t("Удалить группу"))
                .setMessage(Lang.t("Переписка и вложения пропадут у всех. "
                        + "Отменить это нельзя."))
                .setPositiveButton(Lang.t("Удалить"),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                send(Net.frame("delete_group",
                                        "conversation", item.optInt("id")));
                                show(listScreen);
                            }
                        })
                .setNegativeButton(Lang.t("Отмена"), null)
                .show();
    }

    // Куда идёт выбранная фотография: MY_AVATAR — в профиль, номер
    // переписки — фото группы, ноль — обычным сообщением в чат
    private static final int MY_AVATAR = -1;
    private int photoTarget;

    private View personRow(final JSONObject person) {
        // Ниже к имени добавляется @username: по нему человека и ищут
        LinearLayout row = Ui.row(this);
        row.setPadding(Ui.dp(this, 14), Ui.dp(this, 8), Ui.dp(this, 14),
                Ui.dp(this, 8));
        row.addView(Ui.avatar(this, person.optString("name"), Ui.dp(this, 38)));

        LinearLayout подписи = Ui.column(this);
        подписи.setPadding(Ui.dp(this, 12), 0, 0, 0);
        подписи.addView(Ui.text(this, person.optString("name"), 16, Ui.TEXT),
                Ui.wide());
        подписи.addView(Ui.text(this, "@" + person.optString("login"), 13,
                Ui.MUTED), Ui.wide());
        row.addView(подписи, Ui.grow());

        TextView dot = Ui.text(this, "●", 12,
                online.contains(person.optInt("id")) ? Ui.ONLINE : Ui.MUTED);
        row.addView(dot);

        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                // Номер личной переписки знает только сервер: помечаем, кого
                // ждём, и откроем её, когда список обновится
                pendingDirect = person.optInt("id");
                send(Net.frame("direct", "user", person.optInt("id")));
            }
        });
        return row;
    }

    /** Подставляет фотографию вместо кружка с буквой, когда она есть. */
    private void paintPhoto(final TextView view, final String id) {
        // Кружок в шапке один на все переписки: помечаем, чьё фото он ждёт,
        // иначе запоздавшая картинка легла бы на уже другого человека
        view.setTag(id);
        if (id.isEmpty()) {
            return;
        }
        byte[] data = media.get(id);
        if (data != null) {
            view.setText("");
            view.setBackground(new android.graphics.drawable.BitmapDrawable(
                    getResources(), circleBitmap(data)));
            return;
        }
        List<TextView> slots = photoSlots.get(id);
        if (slots == null) {
            slots = new ArrayList<>();
            photoSlots.put(id, slots);
            send(Net.frame("fetch", "id", id));
        }
        slots.add(view);
    }

    private Bitmap circleBitmap(byte[] data) {
        Bitmap source = BitmapFactory.decodeByteArray(data, 0, data.length);
        int side = Math.min(source.getWidth(), source.getHeight());
        Bitmap square = Bitmap.createBitmap(source,
                (source.getWidth() - side) / 2, (source.getHeight() - side) / 2,
                side, side);

        Bitmap round = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888);
        android.graphics.Canvas canvas = new android.graphics.Canvas(round);
        android.graphics.Paint paint = new android.graphics.Paint(
                android.graphics.Paint.ANTI_ALIAS_FLAG);
        paint.setShader(new android.graphics.BitmapShader(square,
                android.graphics.Shader.TileMode.CLAMP,
                android.graphics.Shader.TileMode.CLAMP));
        canvas.drawCircle(side / 2f, side / 2f, side / 2f, paint);
        return round;
    }

    private final Map<String, List<TextView>> photoSlots = new HashMap<>();

    /** Кладёт свежее сообщение в строчку списка переписок. */
    private void rememberLast(JSONObject frame) {
        int where = frame.optInt("conversation");
        for (JSONObject item : conversations) {
            if (item.optInt("id") != where) {
                continue;
            }
            try {
                JSONObject last = new JSONObject();
                last.put("kind", frame.optString("type"));
                last.put("text", frame.optString("text"));
                last.put("nick", frame.optString("nick"));
                last.put("at", frame.optString("at"));
                item.put("last", last);
            } catch (Exception ignored) {
                // Не сложилось — строчка просто останется прежней
            }
            return;
        }
    }

    private String titleOf(JSONObject item) {
        String title = item.optString("title", "");
        return title.isEmpty() ? "Velix" : title;
    }

    private static String cut(String text, int limit) {
        String value = text == null ? "" : text.replace("\n", " ").trim();
        return value.length() <= limit ? value : value.substring(0, limit - 1) + "…";
    }

    // ------------------------------------------------------ экран переписки

    private View buildChat() {
        LinearLayout screen = Ui.column(this);
        screen.setBackgroundColor(Ui.CHAT_BG);

        LinearLayout bar = Ui.row(this);
        bar.setBackgroundColor(Ui.SIDEBAR);
        bar.setPadding(Ui.dp(this, 10), Ui.dp(this, 10), Ui.dp(this, 16),
                Ui.dp(this, 10));

        TextView back = Ui.text(this, "‹", 26, Ui.MUTED);
        back.setPadding(Ui.dp(this, 8), 0, Ui.dp(this, 10), 0);
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                leaveConversation();
            }
        });
        bar.addView(back);

        chatAvatar = Ui.avatar(this, "Velix", Ui.dp(this, 40));
        bar.addView(chatAvatar);

        LinearLayout titles = Ui.column(this);
        titles.setPadding(Ui.dp(this, 12), 0, 0, 0);
        chatTitle = Ui.text(this, "Velix", 17, Ui.TEXT);
        chatStatus = Ui.text(this, "", 12, Ui.MUTED);
        titles.addView(chatTitle, Ui.wide());
        titles.addView(chatStatus, Ui.wide());
        bar.addView(titles, Ui.grow());

        // Вложения ищут не листанием вверх, а вот этой кнопкой
        TextView сетка = Ui.text(this, "🖼", 20, Ui.MUTED);
        сетка.setPadding(Ui.dp(this, 10), 0, Ui.dp(this, 6), 0);
        сетка.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                if (conversation >= 0) {
                    send(Net.frame("gallery", "conversation", conversation));
                }
            }
        });
        bar.addView(сетка);
        screen.addView(bar, Ui.wide());

        pinBar = Ui.row(this);
        pinBar.setBackgroundColor(Ui.SIDEBAR);
        pinBar.setPadding(Ui.dp(this, 16), Ui.dp(this, 8), Ui.dp(this, 16),
                Ui.dp(this, 8));
        pinLabel = Ui.text(this, "", 13, Ui.MUTED);
        pinBar.addView(Ui.text(this, "📌  ", 13, Ui.MUTED));
        pinBar.addView(pinLabel, Ui.grow());
        TextView unpin = Ui.text(this, "✕", 16, Ui.MUTED);
        unpin.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                send(Net.frame("pin", "conversation", conversation, "id",
                        JSONObject.NULL));
            }
        });
        pinBar.addView(unpin);
        pinBar.setVisibility(View.GONE);
        screen.addView(pinBar, Ui.wide());

        feedScroll = new ScrollView(this);
        feed = Ui.column(this);
        feed.setPadding(Ui.dp(this, 10), Ui.dp(this, 8), Ui.dp(this, 10),
                Ui.dp(this, 8));
        feedScroll.addView(feed);
        LinearLayout.LayoutParams feedParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0);
        feedParams.weight = 1;
        screen.addView(feedScroll, feedParams);

        LinearLayout reply = Ui.row(this);
        reply.setBackgroundColor(Ui.SIDEBAR);
        reply.setPadding(Ui.dp(this, 16), Ui.dp(this, 8), Ui.dp(this, 16),
                Ui.dp(this, 8));
        replyLabel = Ui.text(this, "", 13, Ui.MUTED);
        reply.addView(replyLabel, Ui.grow());
        TextView cancel = Ui.text(this, "✕", 16, Ui.MUTED);
        cancel.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                cancelReply();
            }
        });
        reply.addView(cancel);
        reply.setVisibility(View.GONE);
        replyBar = reply;
        screen.addView(reply, Ui.wide());

        LinearLayout composer = Ui.row(this);
        composer.setBackgroundColor(Ui.SIDEBAR);
        composer.setPadding(Ui.dp(this, 10), Ui.dp(this, 8), Ui.dp(this, 10),
                Ui.dp(this, 8));

        TextView attach = Ui.text(this, "+", 24, Ui.MUTED);
        attach.setPadding(Ui.dp(this, 10), 0, Ui.dp(this, 10), 0);
        attach.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                attachMenu();
            }
        });
        composer.addView(attach);

        messageField = Ui.field(this, Lang.t("Написать сообщение…"));
        messageField.addTextChangedListener(new android.text.TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence text, int start,
                                          int count, int after) {
            }

            @Override
            public void onTextChanged(CharSequence text, int start,
                                      int before, int count) {
                // Собеседнику довольно знать, что мы печатаем; чаще раза в
                // две секунды об этом сообщать незачем
                long сейчас = System.currentTimeMillis();
                if (conversation < 0 || сейчас - typingSent < 2000) {
                    return;
                }
                typingSent = сейчас;
                send(Net.frame("typing", "conversation", conversation));
            }

            @Override
            public void afterTextChanged(android.text.Editable text) {
            }
        });
        composer.addView(messageField, Ui.grow());

        // Одна кнопка на голос и на кружочек: нажатие меняет её между ними,
        // зажатие — начинает запись тем, что на ней сейчас нарисовано
        recordButton = Ui.text(this, "🎤", 19, Ui.MUTED);
        recordButton.setPadding(Ui.dp(this, 8), 0, Ui.dp(this, 6), 0);
        recordButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                switchRecordMode();
            }
        });
        recordButton.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View view) {
                startRecording(recordMode);
                return true;
            }
        });
        composer.addView(recordButton);
        paintRecordButton();

        TextView send = Ui.text(this, "➤", 20, Ui.ACCENT);
        send.setPadding(Ui.dp(this, 12), 0, Ui.dp(this, 6), 0);
        send.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                sendText();
            }
        });
        composer.addView(send);
        composerRow = composer;
        screen.addView(composer, Ui.wide());

        // Полоска записи встаёт на место строки ввода: пока идёт запись,
        // писать всё равно нечего
        recordRow = Ui.row(this);
        recordRow.setBackgroundColor(Ui.SIDEBAR);
        recordRow.setGravity(Gravity.CENTER_VERTICAL);
        recordRow.setPadding(Ui.dp(this, 14), Ui.dp(this, 10), Ui.dp(this, 10),
                Ui.dp(this, 10));
        recordDot = Ui.text(this, "●", 16, Ui.DANGER);
        recordRow.addView(recordDot);
        recordLabel = Ui.text(this, "", 15, Ui.TEXT);
        recordLabel.setPadding(Ui.dp(this, 8), 0, 0, 0);
        recordRow.addView(recordLabel, Ui.grow());

        TextView бросить = Ui.text(this, "✕", 18, Ui.MUTED);
        бросить.setPadding(Ui.dp(this, 10), 0, Ui.dp(this, 10), 0);
        бросить.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                cancelRecording();
            }
        });
        recordRow.addView(бросить);

        TextView отправить = Ui.text(this, "➤", 20, Ui.ACCENT);
        отправить.setPadding(Ui.dp(this, 8), 0, Ui.dp(this, 6), 0);
        отправить.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                finishRecording();
            }
        });
        recordRow.addView(отправить);
        recordRow.setVisibility(View.GONE);
        screen.addView(recordRow, Ui.wide());

        return screen;
    }

    private void openConversation(int id) {
        keepDraft();
        conversation = id;
        // Android волен убить приложение, пока человек выбирает файл в
        // галерее. Тогда экран создаётся заново, и без этой пометки он
        // вернулся бы в список, а не в ту переписку, где всё началось.
        prefs().edit().putInt("open", id).apply();
        if (service != null) {
            service.clearUnread(id);
            service.rememberOpen(id);
        }
        cancelReply();
        feed.removeAllViews();
        items.clear();
        ticks.clear();
        bubbles.clear();
        stopVoice();
        stopCircle();
        reactionRows.clear();
        gallery.clear();

        JSONObject item = conversationById(id);
        String title = item == null ? "Velix" : titleOf(item);
        chatTitle.setText(title);
        chatAvatar.setText(Ui.initial(title));
        chatAvatar.setBackground(Ui.circle(Ui.avatarColor(title)));
        if (item != null) {
            paintPhoto(chatAvatar, item.optString("avatar", ""));
        }
        refreshPinBar();

        typingWho = null;
        updateChatStatus();
        restoreDraft();
        if (service != null && service.connected()) {
            send(Net.frame("open", "conversation", id));
        } else {
            showCached(id);
        }
        show(chatScreen);
    }

    /**
     * Строчка под названием переписки.
     *
     * Человеку важнее всего, здесь ли собеседник: печатает ли он прямо
     * сейчас, в сети ли, а если нет — когда заходил.
     */
    private void updateChatStatus() {
        if (chatStatus == null) {
            return;
        }
        if (typingWho != null && System.currentTimeMillis() < typingUntil) {
            chatStatus.setText(Lang.t("{name} печатает…", "name", typingWho));
            return;
        }

        JSONObject item = conversationById(conversation);
        if (item == null) {
            chatStatus.setText("");
            return;
        }
        if ("direct".equals(item.optString("kind"))) {
            int собеседник = item.optInt("user");
            chatStatus.setText(online.contains(собеседник)
                    ? Lang.t("в сети")
                    : Lang.t("был(а) в сети {when}", "when",
                             seenText(seen.get(собеседник))));
            return;
        }
        JSONArray внутри = item.optJSONArray("members");
        if (внутри != null && внутри.length() > 0) {
            chatStatus.setText(Lang.t("участников: {count}", "count",
                    String.valueOf(внутри.length())));
            return;
        }
        chatStatus.setText("");
    }

    /** «только что», «вчера в 21:15», «24 августа в 22:31». */
    private String seenText(String stamp) {
        long когда = parse(stamp);
        if (когда <= 0) {
            return Lang.t("давно");
        }
        long сейчас = System.currentTimeMillis();
        if (сейчас - когда < 90 * 1000L) {
            return Lang.t("только что");
        }

        Calendar был = Calendar.getInstance();
        был.setTimeInMillis(когда);
        Calendar сегодня = Calendar.getInstance();
        String часы = String.format(java.util.Locale.US, "%02d:%02d",
                был.get(Calendar.HOUR_OF_DAY), был.get(Calendar.MINUTE));

        Calendar вчера = Calendar.getInstance();
        вчера.add(Calendar.DAY_OF_YEAR, -1);
        if (сегодня.get(Calendar.YEAR) == был.get(Calendar.YEAR)
                && сегодня.get(Calendar.DAY_OF_YEAR) == был.get(Calendar.DAY_OF_YEAR)) {
            return Lang.t("сегодня в {time}", "time", часы);
        }
        if (вчера.get(Calendar.YEAR) == был.get(Calendar.YEAR)
                && вчера.get(Calendar.DAY_OF_YEAR) == был.get(Calendar.DAY_OF_YEAR)) {
            return Lang.t("вчера в {time}", "time", часы);
        }
        if (сегодня.get(Calendar.YEAR) == был.get(Calendar.YEAR)) {
            return Lang.t("{date} в {time}", "date",
                    Lang.monthDay(был.get(Calendar.DAY_OF_MONTH),
                                  был.get(Calendar.MONTH) + 1),
                    "time", часы);
        }
        return String.format(java.util.Locale.US, "%02d.%02d.%d",
                был.get(Calendar.DAY_OF_MONTH), был.get(Calendar.MONTH) + 1,
                был.get(Calendar.YEAR));
    }

    /** Недописанное переживает и переключение, и закрытие приложения. */
    /**
     * Сохранённая переписка.
     *
     * Связь рвётся в метро и в лифте, а история живёт на сервере — и
     * спросить его в такую минуту не у кого. Поэтому последнее, что он
     * присылал, лежит в файлах приложения и показывается сразу.
     */
    private java.io.File offlineFile(String кусок) {
        java.io.File папка = new java.io.File(getFilesDir(), "offline");
        папка.mkdirs();
        return new java.io.File(папка, кусок + ".json");
    }

    private void keepRooms(JSONArray items) {
        if (items == null) {
            return;
        }
        write(offlineFile("rooms"), items.toString());
    }

    private JSONArray loadRooms() {
        String что = read(offlineFile("rooms"));
        try {
            return что == null ? null : new JSONArray(что);
        } catch (Exception ignored) {
            return null;
        }
    }

    private void keepHistory(int переписка, List<JSONObject> лента) {
        JSONArray что = new JSONArray();
        int начало = Math.max(0, лента.size() - 200);
        for (int место = начало; место < лента.size(); место++) {
            что.put(лента.get(место));
        }
        write(offlineFile("room-" + переписка), что.toString());
    }

    private List<JSONObject> loadHistory(int переписка) {
        List<JSONObject> лента = new ArrayList<>();
        String что = read(offlineFile("room-" + переписка));
        if (что == null) {
            return лента;
        }
        try {
            JSONArray список = new JSONArray(что);
            for (int место = 0; место < список.length(); место++) {
                лента.add(список.optJSONObject(место));
            }
        } catch (Exception ignored) {
            // Файл испортился — покажем пустую ленту, не беда
        }
        return лента;
    }

    private void write(java.io.File куда, String что) {
        try {
            java.io.FileOutputStream поток = new java.io.FileOutputStream(куда);
            поток.write(что.getBytes("UTF-8"));
            поток.close();
        } catch (Exception ignored) {
            // Не сохранилось — на экране всё равно всё есть
        }
    }

    private String read(java.io.File откуда) {
        if (!откуда.exists()) {
            return null;
        }
        try {
            byte[] буфер = new byte[(int) откуда.length()];
            java.io.FileInputStream поток = new java.io.FileInputStream(откуда);
            int прочитано = 0;
            while (прочитано < буфер.length) {
                int шаг = поток.read(буфер, прочитано, буфер.length - прочитано);
                if (шаг <= 0) {
                    break;
                }
                прочитано += шаг;
            }
            поток.close();
            return new String(буфер, 0, прочитано, "UTF-8");
        } catch (Exception ignored) {
            return null;
        }
    }

    /** Рисует ленту из сохранённого и честно говорит, что она такая. */
    private void showCached(int переписка) {
        feed.removeAllViews();
        items.clear();
        ticks.clear();
        bubbles.clear();
        stopVoice();
        stopCircle();
        reactionRows.clear();
        currentDate = null;

        List<JSONObject> лента = loadHistory(переписка);
        drawingHistory = true;
        try {
            for (JSONObject one : лента) {
                items.add(one);
                showItem(one);
            }
        } finally {
            drawingHistory = false;
        }

        TextView слово = Ui.text(this, лента.isEmpty()
                ? Lang.t("нет связи")
                : Lang.t("Нет связи — показываем сохранённое."), 13, Ui.MUTED);
        слово.setGravity(Gravity.CENTER);
        слово.setPadding(0, Ui.dp(this, 12), 0, Ui.dp(this, 4));
        feed.addView(слово, Ui.wide());
        scrollDown();
    }

    /** Показывает сохранённое, пока соединение только устанавливается. */
    private void showSaved() {
        JSONArray сохранённые = loadRooms();
        if (сохранённые == null || сохранённые.length() == 0) {
            return;
        }
        fromCache = true;
        conversations.clear();
        for (int место = 0; место < сохранённые.length(); место++) {
            conversations.add(сохранённые.optJSONObject(место));
        }
        drawList();
        show(listScreen);
    }

    private void keepDraft() {
        if (conversation < 0 || messageField == null || editing > 0) {
            return;
        }
        String текст = messageField.getText().toString().trim();
        if (текст.isEmpty()) {
            drafts.remove(conversation);
        } else {
            drafts.put(conversation, текст);
        }
        JSONObject свёрток = new JSONObject();
        for (Map.Entry<Integer, String> пара : drafts.entrySet()) {
            try {
                свёрток.put(String.valueOf(пара.getKey()), пара.getValue());
            } catch (Exception ignored) {
                // Один черновик не сохранился — остальные всё равно лягут
            }
        }
        prefs().edit().putString("drafts", свёрток.toString()).apply();
    }

    private void restoreDraft() {
        if (messageField == null) {
            return;
        }
        String текст = drafts.get(conversation);
        messageField.setText(текст == null ? "" : текст);
        if (текст != null) {
            messageField.setSelection(messageField.getText().length());
        }
    }

    private void loadDrafts() {
        drafts.clear();
        try {
            JSONObject свёрток = new JSONObject(
                    prefs().getString("drafts", "{}"));
            java.util.Iterator<String> ключи = свёрток.keys();
            while (ключи.hasNext()) {
                String ключ = ключи.next();
                drafts.put(Integer.parseInt(ключ), свёрток.optString(ключ));
            }
        } catch (Exception ignored) {
            // Ничего не разобралось — начнём с чистого листа
        }
    }

    /** Связь вернулась — досылаем написанное, по порядку. */
    private void flushOutbox() {
        if (service == null || !service.connected()) {
            return;
        }
        while (!outbox.isEmpty()) {
            send(outbox.remove(0));
        }
    }

    private JSONObject conversationById(int id) {
        for (JSONObject item : conversations) {
            if (item.optInt("id") == id) {
                return item;
            }
        }
        return null;
    }

    private void sendText() {
        String text = messageField.getText().toString().trim();
        if (text.isEmpty() || conversation < 0) {
            return;
        }
        messageField.setText("");

        if (editing > 0) {
            // Правка уходит вместо нового сообщения; лента поменяется, когда
            // сервер подтвердит — так все увидят одно и то же
            send(Net.frame("edit", "id", editing, "text", text));
            cancelReply();
            return;
        }

        drafts.remove(conversation);
        keepDraft();

        String local = "l" + (++localNumber);
        JSONObject frame = Net.frame("text", "nick", me.optString("name"),
                "text", text, "conversation", conversation, "local", local);
        if (replyTo > 0) {
            try {
                frame.put("reply_to", replyTo);
            } catch (Exception ignored) {
                // Ответ не приложился — сообщение всё равно уйдёт
            }
        }
        if (service != null && service.connected()) {
            send(frame);
        } else {
            // Связи нет: сообщение подождёт в очереди и уйдёт само, когда
            // она вернётся. Раньше оно просто пропадало
            outbox.add(frame);
        }

        JSONObject item = Net.frame("text", "nick", me.optString("name"),
                "text", text, "user", me.optInt("id"), "local", local,
                "at", stamp(), "conversation", conversation);
        if (replyTo > 0) {
            try {
                item.put("reply_to", replyTo);
            } catch (Exception ignored) {
                // См. выше
            }
        }
        items.add(item);
        showItem(item);
        cancelReply();
    }

    private static String stamp() {
        return new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.ROOT)
                .format(new java.util.Date());
    }

    // ----------------------------------------------------------- рисование

    private String lastSender;
    private String currentDate;

    private void showItem(final JSONObject item) {
        // Подсказка «пока тихо» уходит, как только появляется первое сообщение
        if (emptyHint != null) {
            feed.removeView(emptyHint);
            emptyHint = null;
        }

        boolean own = item.optInt("user", -1) == me.optInt("id");
        datePill(item.optString("at"));

        LinearLayout row = Ui.row(this);
        row.setGravity(own ? Gravity.RIGHT : Gravity.LEFT);
        row.setPadding(0, Ui.dp(this, 2), 0, Ui.dp(this, 2));

        if ("deleted".equals(item.optString("kind"))) {
            row.addView(Ui.text(this, Lang.t("сообщение удалено"), 13, Ui.MUTED));
            feed.addView(row, Ui.wide());
            scrollDown();
            return;
        }

        LinearLayout bubble = Ui.column(this);
        android.graphics.drawable.GradientDrawable фон = Ui.rounded(
                own ? Ui.BUBBLE_OUT : Ui.BUBBLE_IN, Ui.dp(this, 14));
        JSONArray позвали = item.optJSONArray("mentions");
        if (позвали != null && me != null) {
            for (int место = 0; место < позвали.length(); место++) {
                if (позвали.optInt(место) == me.optInt("id")) {
                    // Окликнули именно нас: рамка заметна, но не кричит
                    фон.setStroke(Ui.dp(this, 2), Ui.ACCENT);
                    break;
                }
            }
        }
        bubble.setBackground(фон);
        bubble.setPadding(Ui.dp(this, 12), Ui.dp(this, 8), Ui.dp(this, 12),
                Ui.dp(this, 6));

        if (!own) {
            TextView sender = Ui.text(this, item.optString("nick"), 13,
                    Ui.avatarColor(item.optString("nick")));
            bubble.addView(sender, Ui.wide());
        }

        String forwarded = item.optString("forwarded", "");
        if (!forwarded.isEmpty()) {
            bubble.addView(Ui.text(this,
                    Lang.t("Переслано от {name}", "name", forwarded), 12, Ui.MUTED),
                    Ui.wide());
        }

        JSONObject quoted = quotes.get(item.optInt("reply_to", -1));
        if (quoted != null) {
            TextView quote = Ui.text(this, quoted.optString("nick") + ": "
                    + cut(quoted.optString("text", Lang.t("вложение")), 50), 12, Ui.MUTED);
            quote.setBackground(Ui.rounded(Ui.SEPARATOR, Ui.dp(this, 12)));
            quote.setPadding(Ui.dp(this, 8), Ui.dp(this, 4), Ui.dp(this, 8),
                    Ui.dp(this, 4));
            bubble.addView(quote, Ui.wide());
        }

        String kind = item.optString("kind", "text");
        if ("voice".equals(kind) || "circle".equals(kind)) {
            bubble.addView("voice".equals(kind) ? voiceCard(item) : circleCard(item),
                    Ui.wide());
        } else if ("text".equals(kind)) {
            TextView body = Ui.text(this, item.optString("text"), 16, Ui.TEXT);
            bubble.addView(body, Ui.wide());
            if (item.optInt("id", 0) > 0) {
                // Карточка ссылки может приехать позже самого сообщения
                bubbles.put(item.optInt("id"), bubble);
            }
            JSONObject карточка = item.optJSONObject("preview");
            if (карточка != null) {
                linkCard(bubble, карточка);
            }
        } else {
            bubble.addView(attachment(item), Ui.wide());
        }

        LinearLayout footer = Ui.row(this);
        footer.setGravity(Gravity.RIGHT);
        footer.addView(Ui.text(this, time(item.optString("at")), 11, Ui.MUTED));
        if (own) {
            TextView mark = Ui.text(this, "·", 12, Ui.TICK);
            mark.setPadding(Ui.dp(this, 6), 0, 0, 0);
            footer.addView(mark);
            int id = item.optInt("id", -1);
            if (id > 0) {
                ticks.put(id, mark);
                paintTick(id, states.containsKey(id) ? states.get(id)
                        : item.optString("state", "sent"));
            } else {
                pendingTicks.put(item.optString("local"), mark);
            }
        }
        bubble.addView(footer, Ui.wide());

        LinearLayout marks = Ui.row(this);
        bubble.addView(marks, Ui.wide());
        if (item.optInt("id", 0) > 0) {
            reactionRows.put(item.optInt("id"), marks);
            drawReactions(item.optInt("id"));
        } else {
            // Номера ещё нет: свяжем полоску реакций с сообщением, когда
            // сервер подтвердит приём
            pendingMarks.put(item.optString("local"), marks);
        }

        bubble.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View view) {
                messageMenu(item);
                return true;
            }
        });

        row.addView(bubble);
        feed.addView(row, Ui.wide());

        // Пришедшее сообщение въезжает снизу. Всю историю так не показываем:
        // два десятка пузырей, ползущих по очереди, — это мельтешение
        if (!drawingHistory) {
            row.setAlpha(0f);
            row.setTranslationY(Ui.dp(this, 12));
            row.animate().alpha(1f).translationY(0f).setDuration(200).start();
        }
        scrollDown();
    }

    private final Map<String, TextView> pendingTicks = new HashMap<>();
    private final Map<String, LinearLayout> pendingMarks = new HashMap<>();
    private View emptyHint;

    /** Копит вложения переписки по порядку: их листают в полном экране. */
    private void rememberMedia(JSONObject item) {
        String kind = item.optString("kind");
        String id = item.optString("media", "");
        if (id.isEmpty() || !("image".equals(kind) || "gif".equals(kind)
                || "video".equals(kind))) {
            return;
        }
        for (JSONObject уже : gallery) {
            if (id.equals(уже.optString("media"))) {
                return;
            }
        }
        gallery.add(item);
    }

    private int galleryIndex(String mediaId) {
        for (int место = 0; место < gallery.size(); место++) {
            if (gallery.get(место).optString("media").equals(mediaId)) {
                return место;
            }
        }
        return -1;
    }

    private View attachment(final JSONObject item) {
        String kind = item.optString("kind");
        rememberMedia(item);
        if (!"image".equals(kind) && !"gif".equals(kind)) {
            final boolean кино = "video".equals(kind);
            String подпись = кино ? Lang.t("Видео") : Lang.t("Файл");
            long вес = item.optLong("size");
            TextView card = Ui.text(this, подпись + " · "
                    + item.optString("name")
                    + (вес > 0 ? "\n" + humanSize(вес) : "")
                    + (кино ? "\n" + Lang.t("▶ Смотреть") : ""), 15, Ui.TEXT);
            card.setPadding(0, Ui.dp(this, 4), 0, Ui.dp(this, 4));

            final String id = item.optString("media", "");
            final String имя = item.optString("name", "файл");
            if (!id.isEmpty()) {
                card.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View view) {
                        if (кино) {
                            showFull(null, id);
                        } else {
                            saveAttachment(id, имя);
                        }
                    }
                });
            }
            return card;
        }

        final ImageView picture = new ImageView(this);
        picture.setAdjustViewBounds(true);
        picture.setMaxHeight(Ui.dp(this, 320));
        picture.setLayoutParams(new LinearLayout.LayoutParams(Ui.dp(this, 220),
                ViewGroup.LayoutParams.WRAP_CONTENT));

        String id = item.optString("media", "");
        byte[] data = media.get(id);
        if (data == null) {
            data = localMedia.get(item.optString("local", ""));
        }
        if (data != null) {
            picture.setImageBitmap(BitmapFactory.decodeByteArray(data, 0, data.length));
        } else if (!id.isEmpty()) {
            List<ImageView> slots = waiting.get(id);
            if (slots == null) {
                slots = new ArrayList<>();
                waiting.put(id, slots);
                send(Net.frame("fetch", "id", id));
            }
            slots.add(picture);
        }

        picture.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                byte[] bytes = media.get(item.optString("media", ""));
                if (bytes == null) {
                    bytes = localMedia.get(item.optString("local", ""));
                }
                showFull(bytes, item.optString("media", ""));
            }
        });
        return picture;
    }

    private final Map<Integer, LinearLayout> bubbles = new HashMap<>();

    /**
     * Карточка ссылки: сайт, заголовок, выжимка и картинка.
     *
     * По ссылке ходит сервер и присылает уже готовое: иначе каждый, кто
     * просто открыл переписку, засветил бы свой адрес чужому сайту.
     */
    private void linkCard(LinearLayout bubble, JSONObject card) {
        if (bubble.findViewWithTag("velix-card") != null) {
            return;             // карточка уже нарисована
        }

        LinearLayout холст = Ui.column(this);
        холст.setTag("velix-card");
        холст.setBackground(Ui.rounded(Ui.SEPARATOR, Ui.dp(this, 12)));
        холст.setPadding(Ui.dp(this, 10), Ui.dp(this, 8), Ui.dp(this, 10),
                Ui.dp(this, 8));

        String сайт = card.optString("site", "");
        if (!сайт.isEmpty()) {
            холст.addView(Ui.text(this, cut(сайт, 60), 12, Ui.ACCENT), Ui.wide());
        }
        String заголовок = card.optString("title", "");
        if (!заголовок.isEmpty()) {
            холст.addView(Ui.text(this, заголовок, 15, Ui.TEXT), Ui.wide());
        }
        String выжимка = card.optString("text", "");
        if (!выжимка.isEmpty()) {
            холст.addView(Ui.text(this, cut(выжимка, 180), 13, Ui.MUTED),
                    Ui.wide());
        }

        final String картинка = card.optString("image", "");
        if (!картинка.isEmpty()) {
            ImageView вид = new ImageView(this);
            вид.setAdjustViewBounds(true);
            вид.setMaxHeight(Ui.dp(this, 170));
            вид.setScaleType(ImageView.ScaleType.CENTER_CROP);
            вид.setLayoutParams(new LinearLayout.LayoutParams(
                    Ui.dp(this, 220), ViewGroup.LayoutParams.WRAP_CONTENT));
            byte[] данные = media.get(картинка);
            if (данные != null) {
                вид.setImageBitmap(BitmapFactory.decodeByteArray(
                        данные, 0, данные.length));
            } else {
                List<ImageView> слоты = waiting.get(картинка);
                if (слоты == null) {
                    слоты = new ArrayList<>();
                    waiting.put(картинка, слоты);
                    send(Net.frame("fetch", "id", картинка));
                }
                слоты.add(вид);
            }
            холст.addView(вид);
        }

        final String куда = card.optString("url", "");
        if (!куда.isEmpty()) {
            холст.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    openLink(куда);
                }
            });
        }

        LinearLayout.LayoutParams как = Ui.wide();
        как.topMargin = Ui.dp(this, 6);
        bubble.addView(холст, как);
    }

    private void openLink(String куда) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(куда)));
        } catch (Exception ignored) {
            toast(Lang.t("Не получилось открыть ссылку"));
        }
    }

    // ------------------------------------------- голосовые и кружочки
    //
    // Пишет сам телефон: MediaRecorder берёт звук с микрофона (а для
    // кружочка — и картинку с камеры) и сразу сжимает в aac и h264. Ничего
    // стороннего для этого не нужно.

    private LinearLayout composerRow;
    private LinearLayout recordRow;
    private TextView recordLabel;
    private TextView recordDot;

    private MediaRecorder recorder;
    private Camera camera;
    private View cameraOverlay;
    private SurfaceView cameraSurface;
    private int cameraSide;
    private java.io.File recordFile;
    private String recordKind;
    private long recordStarted;
    private Runnable recordTick;
    private MediaPlayer voicePlayer;
    private Runnable voiceTick;
    private MediaPlayer circlePlayer;
    private Runnable circleTick;
    // Что показать, когда приедут байты: первое нажатие иначе уходило впустую
    private final Map<String, Runnable> pendingPlay = new HashMap<>();

    private static final int MAX_VOICE = 300;
    private static final int MAX_CIRCLE = 60;

    private TextView recordButton;
    private String recordMode;
    private boolean toldAboutHolding;

    private void paintRecordButton() {
        if (recordMode == null) {
            recordMode = "circle".equals(prefs().getString("record_mode", "voice"))
                    ? "circle" : "voice";
        }
        if (recordButton != null) {
            recordButton.setText("circle".equals(recordMode) ? "◉" : "🎤");
        }
    }

    /** Короткое нажатие: меняет голос на кружочек и обратно. */
    private void switchRecordMode() {
        recordMode = "voice".equals(recordMode) ? "circle" : "voice";
        prefs().edit().putString("record_mode", recordMode).apply();
        paintRecordButton();

        // Один раз за запуск подсказываем, как записывать: иначе кнопка
        // выглядит так, будто она просто ничего не делает
        if (!toldAboutHolding) {
            toldAboutHolding = true;
            toast(Lang.t("Зажмите кнопку, чтобы записать"));
        }
    }

    private String whatItWas(JSONObject last) {
        String вид = last.optString("kind");
        if ("text".equals(вид)) {
            return last.optString("text");
        }
        if ("voice".equals(вид)) {
            return Lang.t("голосовое");
        }
        if ("circle".equals(вид)) {
            return Lang.t("кружочек");
        }
        return Lang.t("вложение");
    }

    private String clock(long сколько, long всего) {
        String один = (сколько / 60) + ":" + String.format("%02d", сколько % 60);
        if (всего <= 0) {
            return один;
        }
        return один + " / " + (всего / 60) + ":" + String.format("%02d", всего % 60);
    }

    private boolean askedFor(String разрешение) {
        if (checkSelfPermission(разрешение)
                == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            return true;
        }
        requestPermissions(new String[]{разрешение}, 3);
        return false;
    }

    private void startRecording(String kind) {
        if (recorder != null || conversation < 0) {
            return;
        }
        if (!askedFor("android.permission.RECORD_AUDIO")) {
            return;
        }
        if ("circle".equals(kind) && !askedFor("android.permission.CAMERA")) {
            return;
        }

        recordKind = kind;
        recordFile = new java.io.File(getCacheDir(),
                kind + "-" + System.currentTimeMillis()
                + ("voice".equals(kind) ? ".m4a" : ".mp4"));

        if ("circle".equals(kind)) {
            // Камере нужна поверхность, а она появляется не сразу и говорит
            // об этом сюда же, в главный поток. Поэтому не ждём её, а
            // начинаем запись из её же сообщения о готовности
            showCameraThenRecord();
            return;
        }

        try {
            recorder = new MediaRecorder();
            recorder.setAudioSource(MediaRecorder.AudioSource.MIC);
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            recorder.setAudioSamplingRate(48000);
            recorder.setAudioEncodingBitRate(48000);
            recorder.setOutputFile(recordFile.getAbsolutePath());
            recorder.prepare();
            recorder.start();
        } catch (Exception беда) {
            android.util.Log.e("Velix", "голос не пошёл", беда);
            stopEverything();
            toast(Lang.t("Записать не вышло."));
            return;
        }

        beginRecordingBar();
    }

    /** Полоска записи и отсчёт: зовём, когда запись и правда пошла. */
    private void beginRecordingBar() {
        recordStarted = System.currentTimeMillis();
        composerRow.setVisibility(View.GONE);
        recordRow.setVisibility(View.VISIBLE);
        tickRecording();
    }

    /**
     * Показывает предпросмотр и начинает запись, когда поверхность готова.
     *
     * Ждать поверхность нельзя: о её появлении Android сообщает в тот же
     * главный поток, из которого мы бы ждали, — и дождаться было бы нечего.
     * Поэтому вся работа с камерой живёт внутри surfaceCreated.
     */
    private void showCameraThenRecord() {
        cameraSide = Math.min(getResources().getDisplayMetrics().widthPixels,
                Ui.dp(this, 280));

        // Рамка круглая, а картинка внутри прямоугольная: квадратов камера
        // не снимает. Поэтому не втискиваем кадр в квадрат — иначе лицо
        // выходит сплющенным, — а даём ему вылезти за круг и обрезаем
        FrameLayout рамка = new FrameLayout(this);
        FrameLayout.LayoutParams рамкой = new FrameLayout.LayoutParams(
                cameraSide, cameraSide);
        рамкой.gravity = Gravity.CENTER;
        рамка.setLayoutParams(рамкой);
        рамка.setBackgroundColor(Color.BLACK);
        roundOff(рамка, cameraSide);

        final SurfaceView вид = new SurfaceView(this);
        FrameLayout.LayoutParams как = new FrameLayout.LayoutParams(
                cameraSide, cameraSide);
        как.gravity = Gravity.CENTER;
        вид.setLayoutParams(как);
        рамка.addView(вид);
        cameraSurface = вид;

        FrameLayout поверх = new FrameLayout(this);
        поверх.setBackgroundColor(Color.argb(200, 0, 0, 0));
        поверх.addView(рамка);
        root.addView(поверх, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        cameraOverlay = поверх;

        вид.getHolder().addCallback(new SurfaceHolder.Callback() {
            @Override
            public void surfaceCreated(SurfaceHolder holder) {
                if (recorder != null) {
                    return;         // уже пишем: поверхность вернулась дважды
                }
                try {
                    beginCircle(holder);
                } catch (Throwable беда) {
                    // В журнал — целиком, на экран — коротко: по одному
                    // названию исключения не понять ровно ничего
                    android.util.Log.e("Velix", "кружочек не пошёл", беда);
                    String чего = беда.getMessage() != null
                            ? беда.getMessage()
                            : беда.getClass().getSimpleName();
                    stopEverything();
                    toast(Lang.t("Записать не вышло.") + " " + чего);
                    return;
                }
                beginRecordingBar();
            }

            @Override
            public void surfaceChanged(SurfaceHolder holder, int format,
                                       int width, int height) {
            }

            @Override
            public void surfaceDestroyed(SurfaceHolder holder) {
            }
        });
    }

    /**
     * Заводит камеру и запись на уже готовой поверхности.
     *
     * Размеры, кодеки и частоту не назначаем руками, а берём готовым
     * профилем: камера, которой не подошло что-то одно из перечисленного,
     * отвечает отказом на всё сразу — и это тот самый RuntimeException,
     * который не объясняет ничего. Профиль же собран под эту самую камеру.
     */
    private void beginCircle(SurfaceHolder holder) throws Exception {
        int номер = findFrontCamera();
        camera = Camera.open(номер);

        Camera.CameraInfo сведения = new Camera.CameraInfo();
        Camera.getCameraInfo(номер, сведения);
        boolean передняя = сведения.facing == Camera.CameraInfo.CAMERA_FACING_FRONT;
        // Два разных поворота, и их легко перепутать. Тот, что для экрана,
        // у передней камеры ещё и отменяет зеркальность — иначе предпросмотр
        // показывает не то, что видит человек. А записи зеркальность не
        // касается: ей нужен ровно тот угол, о котором говорит сама камера.
        // Подставив туда экранный, я и получил кружочек вверх ногами
        int поворот = передняя
                ? (360 - сведения.orientation % 360) % 360
                : сведения.orientation % 360;
        int подсказка = сведения.orientation % 360;

        camera.setDisplayOrientation(поворот);
        fitPreview(поворот);
        camera.setPreviewDisplay(holder);
        camera.startPreview();
        camera.unlock();

        CamcorderProfile профиль = circleProfile(номер);

        recorder = new MediaRecorder();
        recorder.setCamera(camera);
        recorder.setAudioSource(MediaRecorder.AudioSource.CAMCORDER);
        recorder.setVideoSource(MediaRecorder.VideoSource.CAMERA);
        recorder.setProfile(профиль);
        честныйРазмер(профиль);
        // Кружочек — не кино: полтора мегабита на секунду тут лишние
        recorder.setVideoEncodingBitRate(Math.min(профиль.videoBitRate, 1500000));
        recorder.setOrientationHint(подсказка);
        recorder.setPreviewDisplay(holder.getSurface());
        recorder.setOutputFile(recordFile.getAbsolutePath());
        recorder.prepare();
        recorder.start();
    }

    /** Даёт предпросмотру настоящие пропорции камеры, а не квадрат. */
    private void fitPreview(int поворот) {
        if (cameraSurface == null || camera == null || cameraSide <= 0) {
            return;
        }
        Camera.Size кадр = camera.getParameters().getPreviewSize();
        if (кадр == null || кадр.width <= 0 || кадр.height <= 0) {
            return;
        }
        // При повороте на 90 и 270 ширина с высотой на экране меняются местами
        boolean боком = поворот == 90 || поворот == 270;
        int ширина = боком ? кадр.height : кадр.width;
        int высота = боком ? кадр.width : кадр.height;

        float доля = Math.max((float) cameraSide / ширина,
                              (float) cameraSide / высота);
        FrameLayout.LayoutParams как = new FrameLayout.LayoutParams(
                Math.round(ширина * доля), Math.round(высота * доля));
        как.gravity = Gravity.CENTER;
        cameraSurface.setLayoutParams(как);
    }

    /**
     * Профиль съёмки для кружочка.
     *
     * 720p идёт первым не ради чёткости, а ради пропорций. Профиль 480p на
     * этом телефоне — это 720 на 480: формат родом из телевидения, где
     * пиксель не квадратный. Само по себе это не беда, но MediaRecorder не
     * пишет в файл пометку об этом, и всякий, кто такой файл откроет, честно
     * считает пиксели квадратными — и растягивает лицо на восьмую часть
     * вширь. У 1280 на 720 пиксель квадратный, и растягивать нечего.
     *
     * QUALITY_LOW есть на любом телефоне по определению — им и подстрахуемся.
     */
    private CamcorderProfile circleProfile(int номер) {
        for (int какой : new int[]{CamcorderProfile.QUALITY_720P,
                                   CamcorderProfile.QUALITY_480P,
                                   CamcorderProfile.QUALITY_LOW}) {
            try {
                if (CamcorderProfile.hasProfile(номер, какой)) {
                    return CamcorderProfile.get(номер, какой);
                }
            } catch (Exception ignored) {
                // Эта камера такого профиля не знает — пробуем следующий
            }
        }
        return CamcorderProfile.get(номер, CamcorderProfile.QUALITY_LOW);
    }

    /**
     * Размер кадра с квадратным пикселем.
     *
     * Если выбранный профиль всё-таки телевизионный, сужаем кадр до 640 на
     * 480 — те же четыре к трём, но уже честно, без растягивания.
     */
    private void честныйРазмер(CamcorderProfile профиль) {
        int ширина = профиль.videoFrameWidth;
        int высота = профиль.videoFrameHeight;
        if (ширина == 720 && высота == 480) {
            ширина = 640;
        } else if (ширина == 720 && высота == 576) {
            ширина = 768;       // PAL: та же беда, только в другую сторону
        } else {
            return;             // тут пиксель и так квадратный
        }
        try {
            recorder.setVideoSize(ширина, высота);
        } catch (Exception ignored) {
            // Камера отказалась — пусть снимает как умеет, кривовато, но снимет
        }
    }

    private int findFrontCamera() {
        Camera.CameraInfo сведения = new Camera.CameraInfo();
        for (int номер = 0; номер < Camera.getNumberOfCameras(); номер++) {
            Camera.getCameraInfo(номер, сведения);
            if (сведения.facing == Camera.CameraInfo.CAMERA_FACING_FRONT) {
                return номер;
            }
        }
        return 0;
    }

    /** Обрезает вид кругом: кружочек и должен быть кружочком. */
    private void roundOff(View вид, final int сторона) {
        вид.setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View какой, Outline контур) {
                контур.setOval(0, 0, сторона, сторона);
            }
        });
        вид.setClipToOutline(true);
    }

    private void tickRecording() {
        if (recorder == null) {
            return;
        }
        long прошло = (System.currentTimeMillis() - recordStarted) / 1000;
        int предел = "voice".equals(recordKind) ? MAX_VOICE : MAX_CIRCLE;
        recordLabel.setText(("voice".equals(recordKind)
                ? Lang.t("Записываю голос…") : Lang.t("Записываю кружочек…"))
                + "  " + clock(прошло, 0));
        recordDot.setTextColor(прошло % 2 == 0 ? Ui.DANGER : Ui.SEPARATOR);

        if (прошло >= предел) {
            finishRecording();
            return;
        }
        recordTick = new Runnable() {
            @Override
            public void run() {
                tickRecording();
            }
        };
        main.postDelayed(recordTick, 500);
    }

    private void stopEverything() {
        recordStarted = 0;
        if (recordTick != null) {
            main.removeCallbacks(recordTick);
            recordTick = null;
        }
        if (recorder != null) {
            try {
                recorder.stop();
            } catch (Exception ignored) {
                // Слишком короткая запись — MediaRecorder ругается на stop
            }
            try {
                recorder.release();
            } catch (Exception ignored) {
            }
            recorder = null;
        }
        if (camera != null) {
            try {
                // После записи камера остаётся за MediaRecorder: не забрав её
                // обратно, второй кружочек уже не снять
                camera.lock();
                camera.stopPreview();
            } catch (Exception ignored) {
            }
            camera.release();
            camera = null;
        }
        if (cameraOverlay != null) {
            root.removeView(cameraOverlay);
            cameraOverlay = null;
        }
        cameraSurface = null;
        if (composerRow != null) {
            composerRow.setVisibility(View.VISIBLE);
        }
        if (recordRow != null) {
            recordRow.setVisibility(View.GONE);
        }
    }

    private void cancelRecording() {
        java.io.File был = recordFile;
        stopEverything();
        if (был != null && был.exists()) {
            был.delete();
        }
        recordFile = null;
    }

    private void finishRecording() {
        if (recordStarted == 0) {
            // Зажали и сразу отпустили: камера ещё не открылась
            cancelRecording();
            return;
        }
        java.io.File файл = recordFile;
        String kind = recordKind;
        long секунд = Math.max(1,
                (System.currentTimeMillis() - recordStarted + 500) / 1000);
        stopEverything();
        recordFile = null;

        if (файл == null || !файл.exists() || файл.length() < 512) {
            if (файл != null) {
                файл.delete();
            }
            toast(Lang.t("Записать не вышло."));
            return;
        }

        try {
            byte[] байты = new byte[(int) файл.length()];
            java.io.FileInputStream поток = new java.io.FileInputStream(файл);
            int прочитано = 0;
            while (прочитано < байты.length) {
                int шаг = поток.read(байты, прочитано, байты.length - прочитано);
                if (шаг <= 0) {
                    break;
                }
                прочитано += шаг;
            }
            поток.close();

            String local = "l" + (++localNumber);
            send(Net.frame("media", "nick", me.optString("name"), "kind", kind,
                    "name", файл.getName(), "size", байты.length,
                    "conversation", conversation, "local", local,
                    "seconds", секунд), байты);

            localMedia.put(local, байты);
            JSONObject item = Net.frame("media", "nick", me.optString("name"),
                    "kind", kind, "name", файл.getName(), "size", байты.length,
                    "user", me.optInt("id"), "local", local, "at", stamp(),
                    "seconds", секунд, "conversation", conversation);
            items.add(item);
            showItem(item);
        } catch (Exception беда) {
            toast(Lang.t("Не удалось прочитать файл."));
        } finally {
            файл.delete();
        }
    }

    // ------------------------------------------------------ показ в ленте

    private View voiceCard(final JSONObject item) {
        LinearLayout карточка = Ui.row(this);
        карточка.setGravity(Gravity.CENTER_VERTICAL);
        карточка.setPadding(0, Ui.dp(this, 4), 0, Ui.dp(this, 2));

        final long секунд = item.optLong("seconds");
        final TextView кнопка = Ui.text(this, "▶", 14, Color.WHITE);
        кнопка.setGravity(Gravity.CENTER);
        кнопка.setBackground(Ui.circle(Ui.ACCENT));
        LinearLayout.LayoutParams какая = new LinearLayout.LayoutParams(
                Ui.dp(this, 38), Ui.dp(this, 38));
        какая.rightMargin = Ui.dp(this, 10);
        кнопка.setLayoutParams(какая);
        карточка.addView(кнопка);

        LinearLayout справа = Ui.column(this);
        final ProgressBar полоска = new ProgressBar(this, null,
                android.R.attr.progressBarStyleHorizontal);
        полоска.setMax(1000);
        полоска.setProgress(0);
        справа.addView(полоска, Ui.wide());
        final TextView часы = Ui.text(this, clock(0, секунд), 12, Ui.MUTED);
        справа.addView(часы, Ui.wide());
        карточка.addView(справа, Ui.grow());

        кнопка.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                playVoice(item, кнопка, полоска, часы, секунд);
            }
        });
        return карточка;
    }

    private void playVoice(JSONObject item, final TextView кнопка,
                           final ProgressBar полоска, final TextView часы,
                           final long секунд) {
        if (voicePlayer != null && voicePlayer.isPlaying()) {
            stopVoice();
            кнопка.setText("▶");
            return;
        }

        byte[] данные = media.get(item.optString("media", ""));
        if (данные == null) {
            данные = localMedia.get(item.optString("local", ""));
        }
        if (данные == null) {
            final String id = item.optString("media", "");
            if (id.isEmpty()) {
                return;
            }
            кнопка.setText("…");
            pendingPlay.put(id, new Runnable() {
                @Override
                public void run() {
                    кнопка.setText("▶");
                    playVoice(item, кнопка, полоска, часы, секунд);
                }
            });
            send(Net.frame("fetch", "id", id));
            return;
        }

        try {
            java.io.File где = new java.io.File(getCacheDir(), "voice-play.m4a");
            java.io.FileOutputStream поток = new java.io.FileOutputStream(где);
            поток.write(данные);
            поток.close();

            stopVoice();
            voicePlayer = new MediaPlayer();
            voicePlayer.setDataSource(где.getAbsolutePath());
            voicePlayer.prepare();
            voicePlayer.start();
            кнопка.setText("❚❚");

            voiceTick = new Runnable() {
                @Override
                public void run() {
                    if (voicePlayer == null) {
                        return;
                    }
                    long всего = voicePlayer.getDuration() > 0
                            ? voicePlayer.getDuration() / 1000 : секунд;
                    long сейчас = voicePlayer.getCurrentPosition() / 1000;
                    полоска.setProgress(всего > 0
                            ? (int) (сейчас * 1000 / всего) : 0);
                    часы.setText(clock(сейчас, всего));
                    if (voicePlayer.isPlaying()) {
                        main.postDelayed(voiceTick, 200);
                    }
                }
            };
            main.post(voiceTick);

            voicePlayer.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                @Override
                public void onCompletion(MediaPlayer какой) {
                    кнопка.setText("▶");
                    полоска.setProgress(0);
                    часы.setText(clock(0, секунд));
                    stopVoice();
                }
            });
        } catch (Exception беда) {
            toast(Lang.t("Не удалось открыть голосовое"));
        }
    }

    private void stopVoice() {
        if (voiceTick != null) {
            main.removeCallbacks(voiceTick);
            voiceTick = null;
        }
        if (voicePlayer != null) {
            try {
                voicePlayer.stop();
            } catch (Exception ignored) {
            }
            voicePlayer.release();
            voicePlayer = null;
        }
    }

    /**
     * Кружочек в ленте.
     *
     * Снимает камера прямоугольником — квадратов она не умеет, — поэтому
     * круглую рамку и плёнку внутри разводим: рамка квадратная и обрезает
     * всё круглым, а плёнка внутри крупнее её ровно настолько, чтобы
     * заполнить круг без растягивания. Лишнее уходит за край.
     */
    private View circleCard(final JSONObject item) {
        final int сторона = Ui.dp(this, 200);
        LinearLayout карточка = Ui.column(this);
        карточка.setGravity(Gravity.CENTER_HORIZONTAL);

        FrameLayout рамка = new FrameLayout(this);
        рамка.setLayoutParams(new LinearLayout.LayoutParams(сторона, сторона));
        рамка.setBackgroundColor(Color.BLACK);
        roundOff(рамка, сторона);

        final android.view.TextureView плёнка = new android.view.TextureView(this);
        FrameLayout.LayoutParams как = new FrameLayout.LayoutParams(сторона, сторона);
        как.gravity = Gravity.CENTER;
        плёнка.setLayoutParams(как);
        рамка.addView(плёнка);
        карточка.addView(рамка);

        final TextView часы = Ui.text(this, clock(0, item.optLong("seconds")),
                12, Ui.MUTED);
        часы.setGravity(Gravity.CENTER);
        карточка.addView(часы, Ui.wide());

        рамка.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                playCircle(item, плёнка, часы, сторона);
            }
        });
        return карточка;
    }

    private void playCircle(final JSONObject item,
                            final android.view.TextureView плёнка,
                            final TextView часы, final int сторона) {
        if (circlePlayer != null && circlePlayer.isPlaying()) {
            circlePlayer.pause();
            return;
        }
        if (circlePlayer != null) {
            circlePlayer.start();
            return;
        }

        final String id = item.optString("media", "");
        byte[] данные = media.get(id);
        if (данные == null) {
            данные = localMedia.get(item.optString("local", ""));
        }
        if (данные == null) {
            if (id.isEmpty()) {
                return;
            }
            // Байты ещё не приехали: попросим и вернёмся сюда же, когда
            // приедут, — иначе первое нажатие уходило впустую
            pendingPlay.put(id, new Runnable() {
                @Override
                public void run() {
                    playCircle(item, плёнка, часы, сторона);
                }
            });
            send(Net.frame("fetch", "id", id));
            return;
        }

        if (!плёнка.isAvailable()) {
            // Поверхность ещё не готова — подождём её же сообщения
            final byte[] эти = данные;
            плёнка.setSurfaceTextureListener(
                    new android.view.TextureView.SurfaceTextureListener() {
                @Override
                public void onSurfaceTextureAvailable(
                        android.graphics.SurfaceTexture texture, int w, int h) {
                    startCirclePlayer(эти, плёнка, часы, сторона, item);
                }

                @Override
                public void onSurfaceTextureSizeChanged(
                        android.graphics.SurfaceTexture texture, int w, int h) {
                }

                @Override
                public boolean onSurfaceTextureDestroyed(
                        android.graphics.SurfaceTexture texture) {
                    stopCircle();
                    return true;
                }

                @Override
                public void onSurfaceTextureUpdated(
                        android.graphics.SurfaceTexture texture) {
                }
            });
            return;
        }

        startCirclePlayer(данные, плёнка, часы, сторона, item);
    }

    private void startCirclePlayer(byte[] данные,
                                   final android.view.TextureView плёнка,
                                   final TextView часы, final int сторона,
                                   final JSONObject item) {
        try {
            // Имя файла держим латиницей: кириллица в дорожке к видео
            // однажды уже обернулась «No content provider»
            java.io.File где = new java.io.File(getCacheDir(), "circle-play.mp4");
            java.io.FileOutputStream поток = new java.io.FileOutputStream(где);
            поток.write(данные);
            поток.close();

            stopCircle();
            circlePlayer = new MediaPlayer();
            circlePlayer.setSurface(new android.view.Surface(
                    плёнка.getSurfaceTexture()));
            circlePlayer.setDataSource(где.getAbsolutePath());
            circlePlayer.setOnPreparedListener(new MediaPlayer.OnPreparedListener() {
                @Override
                public void onPrepared(MediaPlayer какой) {
                    fitCircle(плёнка, сторона, какой.getVideoWidth(),
                            какой.getVideoHeight());
                    какой.start();
                    tickCircle(часы, item.optLong("seconds"));
                }
            });
            circlePlayer.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                @Override
                public void onCompletion(MediaPlayer какой) {
                    часы.setText(clock(0, item.optLong("seconds")));
                    stopCircle();
                }
            });
            circlePlayer.prepareAsync();
        } catch (Exception беда) {
            android.util.Log.e("Velix", "кружочек не открылся", беда);
            toast(Lang.t("Не удалось открыть кружочек"));
            stopCircle();
        }
    }

    /**
     * Растягивает кадр так, чтобы он заполнил круг, не сплющившись.
     *
     * Кадр приходит прямоугольным; вписать его в квадрат — значит сплющить,
     * а вписать по короткой стороне и обрезать лишнее — значит показать то,
     * что снимали.
     */
    private void fitCircle(android.view.TextureView плёнка, int сторона,
                           int ширина, int высота) {
        if (ширина <= 0 || высота <= 0) {
            return;
        }
        float доля = Math.max((float) сторона / ширина, (float) сторона / высота);
        android.graphics.Matrix как = new android.graphics.Matrix();
        как.setScale(ширина * доля / сторона, высота * доля / сторона,
                сторона / 2f, сторона / 2f);
        плёнка.setTransform(как);
    }

    private void tickCircle(final TextView часы, final long запасом) {
        circleTick = new Runnable() {
            @Override
            public void run() {
                if (circlePlayer == null) {
                    return;
                }
                long всего = circlePlayer.getDuration() > 0
                        ? circlePlayer.getDuration() / 1000 : запасом;
                часы.setText(clock(circlePlayer.getCurrentPosition() / 1000, всего));
                if (circlePlayer.isPlaying()) {
                    main.postDelayed(circleTick, 200);
                }
            }
        };
        main.post(circleTick);
    }

    private void stopCircle() {
        if (circleTick != null) {
            main.removeCallbacks(circleTick);
            circleTick = null;
        }
        if (circlePlayer != null) {
            try {
                circlePlayer.stop();
            } catch (Exception ignored) {
            }
            circlePlayer.release();
            circlePlayer = null;
        }
    }

    private void datePill(String at) {
        Calendar moment = Calendar.getInstance();
        long millis = parse(at);
        if (millis > 0) {
            moment.setTimeInMillis(millis);
        }
        String key = moment.get(Calendar.YEAR) + "-" + moment.get(Calendar.DAY_OF_YEAR);
        if (key.equals(currentDate)) {
            return;
        }
        currentDate = key;

        Calendar today = Calendar.getInstance();
        boolean isToday = today.get(Calendar.YEAR) == moment.get(Calendar.YEAR)
                && today.get(Calendar.DAY_OF_YEAR) == moment.get(Calendar.DAY_OF_YEAR);
        String caption = isToday ? Lang.t("Сегодня")
                : Lang.monthDay(moment.get(Calendar.DAY_OF_MONTH),
                                moment.get(Calendar.MONTH) + 1);

        TextView pill = Ui.text(this, caption, 12, Ui.MUTED);
        pill.setGravity(Gravity.CENTER);
        pill.setPadding(0, Ui.dp(this, 8), 0, Ui.dp(this, 8));
        feed.addView(pill, Ui.wide());
        lastSender = null;
    }

    private static long parse(String at) {
        if (at == null || at.isEmpty()) {
            return 0;
        }
        try {
            String value = at.replace("Z", "+0000");
            int colon = value.lastIndexOf(':');
            if (colon > 10 && (value.length() - colon) == 3
                    && (value.charAt(colon - 3) == '+' || value.charAt(colon - 3) == '-')) {
                value = value.substring(0, colon) + value.substring(colon + 1);
            }
            int dot = value.indexOf('.');
            if (dot > 0) {
                int end = dot;
                while (end < value.length() && (Character.isDigit(value.charAt(end))
                        || value.charAt(end) == '.')) {
                    end++;
                }
                value = value.substring(0, dot) + value.substring(end);
            }
            return new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.ROOT)
                    .parse(value).getTime();
        } catch (Exception error) {
            return 0;
        }
    }

    private String time(String at) {
        long millis = parse(at);
        return new SimpleDateFormat("HH:mm", Locale.ROOT)
                .format(new java.util.Date(millis > 0 ? millis
                        : System.currentTimeMillis()));
    }

    private void scrollDown() {
        feedScroll.post(new Runnable() {
            @Override
            public void run() {
                feedScroll.fullScroll(View.FOCUS_DOWN);
            }
        });
    }

    // -------------------------------------------------------------- галочки

    private void paintTick(int id, String state) {
        states.put(id, state);
        TextView mark = ticks.get(id);
        if (mark == null) {
            return;
        }
        if ("read".equals(state)) {
            mark.setText("✓✓");
            mark.setTextColor(Ui.TICK_READ);
        } else if ("delivered".equals(state)) {
            mark.setText("✓✓");
            mark.setTextColor(Ui.TICK);
        } else if ("sent".equals(state)) {
            mark.setText("✓");
            mark.setTextColor(Ui.TICK);
        } else {
            mark.setText("·");
            mark.setTextColor(Ui.TICK);
        }
    }

    private void markRead() {
        List<Integer> unread = new ArrayList<>();
        for (JSONObject item : items) {
            int id = item.optInt("id", -1);
            if (id > 0 && item.optInt("user", -1) != me.optInt("id")) {
                unread.add(id);
            }
        }
        if (!unread.isEmpty()) {
            send(Net.frame("read", "conversation", conversation,
                    "ids", Net.numbers(unread)));
        }
    }

    // ---------------------------------------------------------------- меню

    /** Все вложения переписки — сеткой, в отдельном окне. */
    private void showGallery(JSONArray список) {
        final Dialog dialog = new Dialog(this);
        dialog.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE);

        LinearLayout card = Ui.column(this);
        card.setBackground(Ui.rounded(Ui.SIDEBAR, Ui.dp(this, 18)));
        card.setPadding(Ui.dp(this, 10), Ui.dp(this, 10), Ui.dp(this, 10),
                Ui.dp(this, 10));

        int сколько = список == null ? 0 : список.length();
        card.addView(Ui.text(this, сколько == 0
                ? Lang.t("Пока ничего не присылали")
                : Lang.t("Вложения переписки") + " · "
                  + Lang.t("всего: {count}", "count", String.valueOf(сколько)),
                13, Ui.MUTED), Ui.wide());

        // Листаем потом ровно то, что показали здесь, и в том же порядке
        gallery.clear();
        for (int место = сколько - 1; место >= 0; место--) {
            gallery.add(список.optJSONObject(место));
        }

        ScrollView свиток = new ScrollView(this);
        LinearLayout столбец = Ui.column(this);
        свиток.addView(столбец);

        LinearLayout ряд = null;
        final int В_РЯД = 3;
        int сторона = getResources().getDisplayMetrics().widthPixels / 4;
        for (int место = 0; место < gallery.size(); место++) {
            final JSONObject one = gallery.get(место);
            if (место % В_РЯД == 0) {
                ряд = Ui.row(this);
                столбец.addView(ряд, Ui.wide());
            }

            final String номер = one.optString("media", "");
            ImageView клетка = new ImageView(this);
            клетка.setScaleType(ImageView.ScaleType.CENTER_CROP);
            клетка.setBackground(Ui.rounded(Ui.INPUT_BG, Ui.dp(this, 14)));
            LinearLayout.LayoutParams где =
                    new LinearLayout.LayoutParams(сторона, сторона);
            где.setMargins(Ui.dp(this, 3), Ui.dp(this, 3), Ui.dp(this, 3),
                    Ui.dp(this, 3));

            byte[] данные = media.get(номер);
            if (данные != null) {
                клетка.setImageBitmap(BitmapFactory.decodeByteArray(
                        данные, 0, данные.length));
            } else if (!номер.isEmpty()) {
                List<ImageView> ждут = waiting.get(номер);
                if (ждут == null) {
                    ждут = new ArrayList<>();
                    waiting.put(номер, ждут);
                    send(Net.frame("fetch", "id", номер));
                }
                ждут.add(клетка);
            }

            клетка.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    dialog.dismiss();
                    showFull(media.get(номер), номер);
                }
            });
            ряд.addView(клетка, где);
        }

        card.addView(свиток, Ui.grow());
        dialog.setContentView(card);
        dialog.show();
    }

    private void messageMenu(final JSONObject item) {
        final Dialog dialog = new Dialog(this);
        dialog.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE);

        LinearLayout card = Ui.column(this);
        card.setBackground(Ui.rounded(Ui.SIDEBAR, Ui.dp(this, 18)));
        card.setPadding(Ui.dp(this, 8), Ui.dp(this, 8), Ui.dp(this, 8),
                Ui.dp(this, 8));

        final int id = item.optInt("id", -1);
        if (id > 0) {
            LinearLayout strip = Ui.row(this);
            strip.setGravity(Gravity.CENTER);
            for (final String emoji : EMOJI) {
                TextView button = Ui.text(this, emoji, 22, Ui.TEXT);
                button.setPadding(Ui.dp(this, 10), Ui.dp(this, 6), Ui.dp(this, 10),
                        Ui.dp(this, 6));
                button.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View view) {
                        send(Net.frame("react", "id", id, "emoji", emoji));
                        dialog.dismiss();
                    }
                });
                strip.addView(button);
            }
            card.addView(strip, Ui.wide());
        }

        card.addView(menuRow("↩", Lang.t("Ответить"), dialog, new Runnable() {
            @Override
            public void run() {
                startReply(item);
            }
        }), Ui.wide());

        if (id > 0) {
            JSONObject current = pinned.get(String.valueOf(conversation));
            boolean isPinned = current != null && current.optInt("id") == id;
            card.addView(menuRow("📌", isPinned ? Lang.t("Открепить")
                    : Lang.t("Закрепить"), dialog, new Runnable() {
                @Override
                public void run() {
                    JSONObject frame = Net.frame("pin", "conversation", conversation);
                    try {
                        frame.put("id", pinned.containsKey(String.valueOf(conversation))
                                && pinned.get(String.valueOf(conversation)).optInt("id") == id
                                ? JSONObject.NULL : id);
                    } catch (Exception ignored) {
                        // Кадр всё равно уйдёт с закреплением
                    }
                    send(frame);
                }
            }), Ui.wide());

            card.addView(menuRow("⧉", copyLabel(item), dialog, new Runnable() {
                @Override
                public void run() {
                    copyItem(item);
                }
            }), Ui.wide());

            card.addView(menuRow("↪", Lang.t("Переслать"), dialog, new Runnable() {
                @Override
                public void run() {
                    forwardMenu(item);
                }
            }), Ui.wide());
        }

        if (id > 0 && item.optInt("user", -1) == me.optInt("id")
                && "text".equals(item.optString("kind", "text"))) {
            card.addView(menuRow("✎", Lang.t("Изменить"), dialog, new Runnable() {
                @Override
                public void run() {
                    startEdit(item);
                }
            }), Ui.wide());
        }

        if (id > 0 && item.optInt("user", -1) == me.optInt("id")) {
            card.addView(menuRow("🗑", Lang.t("Удалить"), dialog, new Runnable() {
                @Override
                public void run() {
                    send(Net.frame("delete", "id", id));
                }
            }), Ui.wide());
        }

        dialog.setContentView(card);
        if (dialog.getWindow() != null) {
            dialog.getWindow().setBackgroundDrawable(
                    new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));
            dialog.getWindow().setLayout(
                    (int) (getResources().getDisplayMetrics().widthPixels * 0.86),
                    ViewGroup.LayoutParams.WRAP_CONTENT);
        }
        dialog.show();
    }

    private View menuRow(String icon, String label, final Dialog dialog,
                         final Runnable action) {
        LinearLayout row = Ui.row(this);
        row.setPadding(Ui.dp(this, 12), Ui.dp(this, 12), Ui.dp(this, 12),
                Ui.dp(this, 12));
        row.addView(Ui.text(this, icon + "   ", 16, Ui.MUTED));
        row.addView(Ui.text(this, label, 16, Ui.TEXT), Ui.grow());
        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                dialog.dismiss();
                action.run();
            }
        });
        return row;
    }

    private String copyLabel(JSONObject item) {
        String kind = item.optString("kind", "text");
        if ("text".equals(kind)) {
            return Lang.t("Копировать текст");
        }
        if ("image".equals(kind) || "gif".equals(kind)) {
            return Lang.t("Копировать фото");
        }
        return Lang.t("Копировать файл");
    }

    private void copyItem(JSONObject item) {
        ClipboardManager clipboard =
                (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        String kind = item.optString("kind", "text");

        if ("text".equals(kind)) {
            clipboard.setPrimaryClip(ClipData.newPlainText("velix",
                    item.optString("text")));
            toast(Lang.t("Скопировано"));
            return;
        }

        byte[] data = media.get(item.optString("media", ""));
        if (data == null) {
            clipboard.setPrimaryClip(ClipData.newPlainText("velix",
                    item.optString("name")));
            toast(Lang.t("Скопировано"));
            return;
        }

        // Картинку в буфер обмена кладут через ссылку на файл, поэтому
        // сохраняем её в галерею и копируем адрес: так фото остаётся у человека
        Bitmap picture = BitmapFactory.decodeByteArray(data, 0, data.length);
        String saved = android.provider.MediaStore.Images.Media.insertImage(
                getContentResolver(), picture, item.optString("name", "velix"), "");
        clipboard.setPrimaryClip(ClipData.newPlainText("velix", saved));
        toast(Lang.t("Скопировано"));
    }

    private void startReply(JSONObject item) {
        replyTo = item.optInt("id", -1);
        if (replyTo <= 0) {
            return;
        }
        String what = "text".equals(item.optString("kind", "text"))
                ? item.optString("text") : item.optString("name");
        replyLabel.setText(Lang.t("Ответ {name}", "name", item.optString("nick"))
                + ": " + cut(what, 40));
        replyBar.setVisibility(View.VISIBLE);
    }

    private void cancelReply() {
        replyTo = -1;
        editing = 0;
        replyBar.setVisibility(View.GONE);
    }

    /** Текст возвращается в ту же строку ввода — там его и правят. */
    private void startEdit(JSONObject item) {
        editing = item.optInt("id", 0);
        if (editing <= 0) {
            return;
        }
        replyTo = -1;
        replyLabel.setText(Lang.t("Правим: {text}", "text",
                cut(item.optString("text"), 40)));
        replyBar.setVisibility(View.VISIBLE);
        messageField.setText(item.optString("text"));
        messageField.setSelection(messageField.getText().length());
        messageField.requestFocus();
    }

    private void forwardMenu(final JSONObject item) {
        final List<JSONObject> targets = new ArrayList<>();
        List<String> names = new ArrayList<>();
        for (JSONObject one : conversations) {
            if (one.optInt("id") != conversation) {
                targets.add(one);
                names.add(titleOf(one));
            }
        }
        if (targets.isEmpty()) {
            return;
        }

        new AlertDialog.Builder(this)
                .setTitle(Lang.t("Куда переслать"))
                .setItems(names.toArray(new String[0]),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                send(Net.frame("forward", "id", item.optInt("id"),
                                        "conversation", targets.get(which).optInt("id")));
                            }
                        })
                .show();
    }

    /**
     * Снимок во весь экран: щипком приближается, пальцем двигается.
     *
     * Картинку двигаем матрицей самого вида — так не приходится собирать
     * новый растр на каждое движение пальца.
     */
    private void showFull(byte[] data, final String mediaId) {
        final Dialog dialog = new Dialog(this,
                android.R.style.Theme_Black_NoTitleBar_Fullscreen);
        final FrameLayout stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.BLACK);

        final int[] место = {galleryIndex(mediaId)};
        final byte[][] своё = {data};
        final TextView counter = Ui.text(this, "", 13, Ui.MUTED);
        counter.setPadding(Ui.dp(this, 14), Ui.dp(this, 14), Ui.dp(this, 14), 0);

        final Runnable[] нарисовать = new Runnable[1];
        нарисовать[0] = new Runnable() {
            @Override
            public void run() {
                stage.removeAllViews();
                counter.setText(gallery.size() > 1 && место[0] >= 0
                        ? (место[0] + 1) + " / " + gallery.size() : "");

                JSONObject item = место[0] >= 0 && место[0] < gallery.size()
                        ? gallery.get(место[0]) : null;
                String вид = item == null ? "image" : item.optString("kind");
                String номер = item == null ? mediaId : item.optString("media", "");

                byte[] байты = своё[0];
                if (item != null) {
                    байты = media.get(номер);
                    if (байты == null) {
                        байты = localMedia.get(item.optString("local", ""));
                    }
                }

                if ("video".equals(вид)) {
                    playVideo(stage, номер, item);
                } else if (байты != null) {
                    stage.addView(zoomable(dialog, байты, нарисовать[0], место),
                            new FrameLayout.LayoutParams(
                                    ViewGroup.LayoutParams.MATCH_PARENT,
                                    ViewGroup.LayoutParams.MATCH_PARENT));
                } else {
                    TextView ждём = Ui.text(MainActivity.this,
                            Lang.t("загружаю…"), 15, Ui.MUTED);
                    stage.addView(ждём, серединка());
                    if (!номер.isEmpty()) {
                        viewerWaiting = номер;
                        viewerRepaint = нарисовать[0];
                        send(Net.frame("fetch", "id", номер));
                    }
                }

                stage.addView(counter, новая(Gravity.TOP | Gravity.START));
                if (gallery.size() > 1) {
                    stage.addView(стрелка("\u2039", -1, место, нарисовать[0]),
                            новая(Gravity.CENTER_VERTICAL | Gravity.START));
                    stage.addView(стрелка("\u203a", 1, место, нарисовать[0]),
                            новая(Gravity.CENTER_VERTICAL | Gravity.END));
                }
            }
        };

        dialog.setContentView(stage);
        dialog.setOnDismissListener(new android.content.DialogInterface.OnDismissListener() {
            @Override
            public void onDismiss(android.content.DialogInterface which) {
                viewerWaiting = "";
                viewerRepaint = null;
            }
        });
        нарисовать[0].run();
        dialog.show();
    }

    private void showFull(byte[] data) {
        showFull(data, "");
    }

    private FrameLayout.LayoutParams серединка() {
        FrameLayout.LayoutParams где = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        где.gravity = Gravity.CENTER;
        return где;
    }

    private FrameLayout.LayoutParams новая(int gravity) {
        FrameLayout.LayoutParams где = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        где.gravity = gravity;
        return где;
    }

    /** Кнопка «предыдущее» или «следующее» по краю экрана. */
    private TextView стрелка(String знак, final int шаг, final int[] место,
                             final Runnable нарисовать) {
        TextView кнопка = Ui.text(this, знак, 30, Ui.TEXT);
        кнопка.setPadding(Ui.dp(this, 18), Ui.dp(this, 22), Ui.dp(this, 18),
                Ui.dp(this, 22));
        кнопка.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                шагнуть(шаг, место, нарисовать);
            }
        });
        return кнопка;
    }

    private void шагнуть(int шаг, int[] место, Runnable нарисовать) {
        if (gallery.size() < 2) {
            return;
        }
        int откуда = место[0] < 0 ? 0 : место[0];
        место[0] = (откуда + шаг + gallery.size()) % gallery.size();
        нарисовать.run();
    }

    /** Снимок с приближением щипком и листанием смахиванием. */
    private ImageView zoomable(final Dialog dialog, byte[] data,
                               final Runnable нарисовать, final int[] место) {
        final ImageView picture = new ImageView(this);
        picture.setBackgroundColor(Color.BLACK);
        picture.setScaleType(ImageView.ScaleType.MATRIX);
        picture.setImageBitmap(BitmapFactory.decodeByteArray(data, 0, data.length));

        final android.graphics.Matrix холст = new android.graphics.Matrix();
        final float[] числа = new float[9];
        final boolean[] уложили = {false};

        // Вписываем снимок в экран, как только тот измерился
        picture.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            @Override
            public void onLayoutChange(View view, int left, int top, int right,
                                       int bottom, int a, int b, int c, int d) {
                if (уложили[0] || picture.getDrawable() == null) {
                    return;
                }
                уложили[0] = true;
                float ширина = picture.getDrawable().getIntrinsicWidth();
                float высота = picture.getDrawable().getIntrinsicHeight();
                float во_сколько = Math.min((right - left) / ширина,
                        (bottom - top) / высота);
                холст.setScale(во_сколько, во_сколько);
                холст.postTranslate(((right - left) - ширина * во_сколько) / 2f,
                        ((bottom - top) - высота * во_сколько) / 2f);
                picture.setImageMatrix(холст);
            }
        });

        final float[] вписано = {1f};
        final ScaleGestureDetector щипок = new ScaleGestureDetector(this,
                new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override
            public boolean onScale(ScaleGestureDetector detector) {
                холст.getValues(числа);
                float сейчас = числа[android.graphics.Matrix.MSCALE_X];
                float во_сколько = detector.getScaleFactor();

                // Дальше вписанного не отпускаем и восьмикратного не пускаем
                float нижний = вписано[0] * 0.9f;
                float верхний = вписано[0] * 8f;
                if (сейчас * во_сколько < нижний) {
                    во_сколько = нижний / сейчас;
                } else if (сейчас * во_сколько > верхний) {
                    во_сколько = верхний / сейчас;
                }
                холст.postScale(во_сколько, во_сколько, detector.getFocusX(),
                        detector.getFocusY());
                picture.setImageMatrix(холст);
                return true;
            }
        });

        final GestureDetector жесты = new GestureDetector(this,
                new GestureDetector.SimpleOnGestureListener() {
            @Override
            public boolean onDoubleTap(MotionEvent event) {
                холст.getValues(числа);
                float сейчас = числа[android.graphics.Matrix.MSCALE_X];
                float во_сколько = сейчас > вписано[0] * 1.1f
                        ? вписано[0] / сейчас : 2.5f;
                холст.postScale(во_сколько, во_сколько, event.getX(), event.getY());
                picture.setImageMatrix(холст);
                return true;
            }

            @Override
            public boolean onScroll(MotionEvent от, MotionEvent до,
                                    float сдвигX, float сдвигY) {
                холст.getValues(числа);
                if (числа[android.graphics.Matrix.MSCALE_X] <= вписано[0] * 1.1f) {
                    return false;   // неприближённый снимок таскать некуда
                }
                холст.postTranslate(-сдвигX, -сдвигY);
                picture.setImageMatrix(холст);
                return true;
            }

            @Override
            public boolean onFling(MotionEvent от, MotionEvent до,
                                   float скоростьX, float скоростьY) {
                холст.getValues(числа);
                if (числа[android.graphics.Matrix.MSCALE_X] > вписано[0] * 1.1f) {
                    return false;   // приближённое смахивание — это перетаскивание
                }
                if (от == null || до == null
                        || Math.abs(до.getX() - от.getX()) < Ui.dp(MainActivity.this, 60)
                        || Math.abs(скоростьX) < Math.abs(скоростьY)) {
                    return false;
                }
                шагнуть(до.getX() < от.getX() ? 1 : -1, место, нарисовать);
                return true;
            }

            @Override
            public boolean onSingleTapConfirmed(MotionEvent event) {
                холст.getValues(числа);
                if (числа[android.graphics.Matrix.MSCALE_X] <= вписано[0] * 1.1f) {
                    dialog.dismiss();     // неприближённый снимок закрываем
                }
                return true;
            }
        });

        picture.setOnTouchListener(new View.OnTouchListener() {
            @Override
            public boolean onTouch(View view, MotionEvent event) {
                if (вписано[0] == 1f && уложили[0]) {
                    холст.getValues(числа);
                    вписано[0] = числа[android.graphics.Matrix.MSCALE_X];
                }
                щипок.onTouchEvent(event);
                жесты.onTouchEvent(event);
                return true;
            }
        });
        return picture;
    }

    /** Ролик прямо в приложении: обычный проигрыватель с полосой. */
    private void playVideo(FrameLayout stage, String номер, JSONObject item) {
        String путь = videoFiles.get(номер);
        if (путь == null || !new java.io.File(путь).exists()) {
            byte[] байты = media.get(номер);
            if (байты != null) {
                путь = keepVideo(номер, байты);
            }
        }

        if (путь == null) {
            TextView ждём = Ui.text(this, Lang.t("загружаю…"), 15, Ui.MUTED);
            stage.addView(ждём, серединка());
            if (!номер.isEmpty() && !waitingVideos.containsKey(номер)) {
                waitingVideos.put(номер, item == null ? "video.mp4"
                        : item.optString("name", "video.mp4"));
                send(Net.frame("fetch", "id", номер));
            }
            viewerWaiting = номер;
            return;
        }

        android.widget.VideoView экран = new android.widget.VideoView(this);
        экран.setVideoPath(путь);
        android.widget.MediaController пульт = new android.widget.MediaController(this);
        пульт.setAnchorView(экран);
        экран.setMediaController(пульт);
        экран.setOnPreparedListener(new android.media.MediaPlayer.OnPreparedListener() {
            @Override
            public void onPrepared(android.media.MediaPlayer player) {
                player.setLooping(false);
            }
        });
        экран.setOnErrorListener(new android.media.MediaPlayer.OnErrorListener() {
            @Override
            public boolean onError(android.media.MediaPlayer player, int what, int extra) {
                toast(Lang.t("Видео не открылось"));
                return true;
            }
        });

        FrameLayout.LayoutParams где = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT);
        где.gravity = Gravity.CENTER;
        stage.addView(экран, где);
        экран.start();
    }

    /** Кладёт ролик в кэш: проигрывателю нужен путь, а не байты. */
    private String keepVideo(String номер, byte[] данные) {
        try {
            java.io.File папка = new java.io.File(getCacheDir(), "video");
            папка.mkdirs();
            java.io.File файл = new java.io.File(папка, "velix-" + номер + ".mp4");
            if (!файл.exists() || файл.length() != данные.length) {
                java.io.FileOutputStream поток = new java.io.FileOutputStream(файл);
                поток.write(данные);
                поток.close();
            }
            videoFiles.put(номер, файл.getAbsolutePath());
            return файл.getAbsolutePath();
        } catch (Exception беда) {
            return null;
        }
    }

    /** Кто ждёт больших вложений: номер -> имя, под которым сохранить. */
    private final Map<String, String> waitingFiles = new HashMap<>();

    /** Просит вложение и обещает положить его в «Загрузки». */
    private void saveAttachment(String id, String name) {
        waitingFiles.put(id, name);
        toast(Lang.t("Скачиваю «{name}»…", "name", name));
        send(Net.frame("fetch", "id", id));
    }

    /**
     * Кладёт скачанное в общие «Загрузки».
     *
     * Своим каталогом делиться с другими приложениями нельзя без отдельного
     * поставщика, а в «Загрузках» файл виден любому проигрывателю.
     */
    private void storeInDownloads(java.io.File готовое, String name) {
        try {
            android.content.ContentValues поля = new android.content.ContentValues();
            поля.put(android.provider.MediaStore.Downloads.DISPLAY_NAME, name);
            поля.put(android.provider.MediaStore.Downloads.MIME_TYPE,
                    "application/octet-stream");

            Uri куда = getContentResolver().insert(
                    android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, поля);
            if (куда == null) {
                toast(Lang.t("Не удалось сохранить файл."));
                return;
            }

            java.io.InputStream откуда = new java.io.FileInputStream(готовое);
            java.io.OutputStream поток = getContentResolver().openOutputStream(куда);
            byte[] буфер = new byte[64 * 1024];
            int прочитано;
            while ((прочитано = откуда.read(буфер)) > 0) {
                поток.write(буфер, 0, прочитано);
            }
            откуда.close();
            поток.close();
            готовое.delete();
            toast(Lang.t("«{name}» сохранён в Загрузки", "name", name));
        } catch (Exception error) {
            toast(Lang.t("Не удалось сохранить файл."));
        }
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    // ------------------------------------------------------------ реакции

    private void drawReactions(int id) {
        LinearLayout holder = reactionRows.get(id);
        if (holder == null) {
            return;
        }
        holder.removeAllViews();

        JSONObject summary = reactions.get(id);
        if (summary == null) {
            return;
        }
        java.util.Iterator<String> keys = summary.keys();
        while (keys.hasNext()) {
            final String emoji = keys.next();
            JSONArray who = summary.optJSONArray(emoji);
            int count = who == null ? 0 : who.length();
            if (count == 0) {
                continue;
            }
            TextView mark = Ui.text(this, emoji + " " + count, 13, Ui.TEXT);
            mark.setBackground(Ui.rounded(Ui.INPUT_BG, Ui.dp(this, 14)));
            mark.setPadding(Ui.dp(this, 8), Ui.dp(this, 3), Ui.dp(this, 8),
                    Ui.dp(this, 3));
            final int messageId = id;
            mark.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    send(Net.frame("react", "id", messageId, "emoji", emoji));
                }
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            params.setMargins(0, Ui.dp(this, 4), Ui.dp(this, 4), 0);
            holder.addView(mark, params);
        }
    }

    // -------------------------------------------------------- группы и фото

    private void newGroup() {
        final List<JSONObject> others = new ArrayList<>();
        for (JSONObject person : people) {
            if (person.optInt("id") != me.optInt("id")) {
                others.add(person);
            }
        }
        if (others.isEmpty()) {
            toast(Lang.t("Пока некого позвать в группу."));
            return;
        }

        LinearLayout card = Ui.column(this);
        card.setPadding(Ui.dp(this, 20), Ui.dp(this, 12), Ui.dp(this, 20), 0);

        final EditText title = Ui.field(this, Lang.t("Название группы"));
        card.addView(title, Ui.wide());

        final List<CheckBox> boxes = new ArrayList<>();
        for (JSONObject person : others) {
            CheckBox box = new CheckBox(this);
            box.setText(person.optString("name"));
            box.setTextColor(Ui.TEXT);
            boxes.add(box);
            card.addView(box, Ui.wide());
        }

        new AlertDialog.Builder(this)
                .setTitle(Lang.t("Новая группа"))
                .setView(card)
                .setPositiveButton(Lang.t("Создать"),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                JSONArray members = new JSONArray();
                                for (int index = 0; index < boxes.size(); index++) {
                                    if (boxes.get(index).isChecked()) {
                                        members.put(others.get(index).optInt("id"));
                                    }
                                }
                                if (title.getText().toString().trim().isEmpty()
                                        || members.length() == 0) {
                                    return;
                                }
                                pendingGroup = true;
                                send(Net.frame("group", "title",
                                        title.getText().toString().trim(),
                                        "members", members));
                            }
                        })
                .setNegativeButton(Lang.t("Отмена"), null)
                .show();
    }

    private void pickPhoto() {
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.setType("image/*");
        startActivityForResult(intent, PICK_PHOTO);
    }

    /** Что прикладываем: фотографию или любой файл. */
    private void attachMenu() {
        final String[] выбор = {Lang.t("Фото"), Lang.t("Видео или файл")};
        new AlertDialog.Builder(this)
                .setItems(выбор, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        if (which == 0) {
                            pickPhoto();
                        } else {
                            pickFile();
                        }
                    }
                })
                .show();
    }

    private void pickFile() {
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.setType("*/*");
        startActivityForResult(intent, PICK_FILE);
    }

    /** Имя и вес выбранного файла — их знает не сам адрес, а поставщик. */
    private String[] describe(Uri uri) {
        String name = uri.getLastPathSegment();
        long size = -1;
        android.database.Cursor cursor = getContentResolver()
                .query(uri, null, null, null, null);
        if (cursor != null) {
            try {
                if (cursor.moveToFirst()) {
                    int столбец = cursor.getColumnIndex(
                            android.provider.OpenableColumns.DISPLAY_NAME);
                    if (столбец >= 0 && cursor.getString(столбец) != null) {
                        name = cursor.getString(столбец);
                    }
                    int вес = cursor.getColumnIndex(
                            android.provider.OpenableColumns.SIZE);
                    if (вес >= 0 && !cursor.isNull(вес)) {
                        size = cursor.getLong(вес);
                    }
                }
            } finally {
                cursor.close();
            }
        }
        if (name == null || name.isEmpty()) {
            name = "файл";
        }
        return new String[]{name, String.valueOf(size)};
    }

    /**
     * Отправка большого вложения: сперва заявка, потом куски.
     *
     * Гигабайтное видео нельзя ни прочитать в память, ни послать одним
     * кадром, поэтому читаем его порциями прямо во время отправки.
     */
    private void sendBigFile(Uri uri) {
        if (service == null || conversation < 0) {
            // Служба ещё не привязалась после пересоздания экрана —
            // отправим, как только всё встанет на место
            pendingUpload = uri;
            return;
        }

        String[] описание = describe(uri);
        String name = описание[0];
        long size = Long.parseLong(описание[1]);

        if (size <= 0) {
            toast(Lang.t("Не удалось прочитать файл: {error}", "error", name));
            return;
        }

        long предел = limitFor(kindOf(name));
        if (size > предел) {
            toast(Lang.t("«{name}» весит {size}, а больше {limit} сервер не принимает.",
                    "name", name, "size", humanSize(size),
                    "limit", humanSize(предел)));
            return;
        }

        String local = "l" + (++localNumber);
        pendingUploads.put(local, new Object[]{uri, name, size});

        uploadLine = Ui.text(this, Lang.t("Отправляю «{name}» — {percent}%",
                "name", name, "percent", "0"), 12, Ui.MUTED);
        uploadLine.setGravity(Gravity.CENTER);
        uploadLine.setPadding(0, Ui.dp(this, 8), 0, Ui.dp(this, 8));
        feed.addView(uploadLine, Ui.wide());

        send(Net.frame("upload", "name", name, "size", size,
                "conversation", conversation, "local", local));
    }

    private void pushChunks(final String ticket, final Uri uri, final int chunk) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    InputStream stream = getContentResolver().openInputStream(uri);
                    byte[] буфер = new byte[chunk];
                    int прочитано;
                    while ((прочитано = stream.read(буфер)) > 0) {
                        byte[] кусок = прочитано == буфер.length ? буфер.clone()
                                : java.util.Arrays.copyOf(буфер, прочитано);
                        send(Net.frame("chunk", "ticket", ticket), кусок);
                    }
                    stream.close();
                } catch (Exception error) {
                    main.post(new Runnable() {
                        @Override
                        public void run() {
                            toast(Lang.t("Не удалось прочитать файл: {error}",
                                    "error", String.valueOf(error.getMessage())));
                        }
                    });
                }
            }
        }).start();
    }

    private String kindOf(String name) {
        String конец = name.toLowerCase();
        int точка = конец.lastIndexOf('.');
        конец = точка < 0 ? "" : конец.substring(точка + 1);
        if (конец.equals("gif")) {
            return "gif";
        }
        for (String один : new String[]{"png", "jpg", "jpeg", "webp", "bmp"}) {
            if (один.equals(конец)) {
                return "image";
            }
        }
        for (String один : new String[]{"mp4", "mov", "webm", "mkv", "avi", "m4v"}) {
            if (один.equals(конец)) {
                return "video";
            }
        }
        return "file";
    }

    private long limitFor(String kind) {
        if ("video".equals(kind)) {
            return limits.optLong("video", 1024L * 1024 * 1024);
        }
        if ("image".equals(kind) || "gif".equals(kind)) {
            return limits.optLong("image", Net.MAX_MEDIA);
        }
        return limits.optLong("file", 500L * 1024 * 1024);
    }

    static String humanSize(long size) {
        if (size < 1024) {
            return size + " " + Lang.t("Б");
        }
        if (size < 1024 * 1024) {
            return (size / 1024) + " " + Lang.t("КБ");
        }
        if (size < 1024L * 1024 * 1024) {
            return String.format(java.util.Locale.US, "%.1f %s",
                    size / 1024.0 / 1024.0, Lang.t("МБ"));
        }
        return String.format(java.util.Locale.US, "%.1f %s",
                size / 1024.0 / 1024.0 / 1024.0, Lang.t("ГБ"));
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (result != RESULT_OK || data == null || data.getData() == null
                || (request != PICK_PHOTO && request != PICK_FILE)) {
            super.onActivityResult(request, result, data);
            return;
        }

        if (request == PICK_FILE) {
            String имя = describe(data.getData())[0];
            String вид = kindOf(имя);
            // Картинку и здесь отправляем целиком: сервер её ужмёт
            if (!"image".equals(вид) && !"gif".equals(вид)) {
                sendBigFile(data.getData());
                return;
            }
        }

        Uri uri = data.getData();
        try {
            InputStream stream = getContentResolver().openInputStream(uri);
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[16384];
            int read;
            while ((read = stream.read(chunk)) > 0) {
                buffer.write(chunk, 0, read);
            }
            stream.close();
            byte[] bytes = buffer.toByteArray();

            String name = uri.getLastPathSegment();
            if (name == null || !name.contains(".")) {
                name = "photo.jpg";
            }
            if (bytes.length > Net.MAX_MEDIA) {
                toast(Lang.t("«{name}» больше 25 МБ, сервер такое не принимает.",
                        "name", name));
                return;
            }

            if (photoTarget == MY_AVATAR) {
                // Своя аватарка: без номера переписки сервер понимает,
                // что это профиль, а не фото группы
                send(Net.frame("avatar", "name", name, "size", bytes.length),
                        bytes);
                photoTarget = 0;
                return;
            }

            if (photoTarget > 0) {
                send(Net.frame("avatar", "conversation", photoTarget,
                        "name", name, "size", bytes.length), bytes);
                photoTarget = 0;
                return;
            }

            String local = "l" + (++localNumber);
            send(Net.frame("media", "nick", me.optString("name"), "kind", "image",
                    "name", name, "size", bytes.length, "conversation", conversation,
                    "local", local), bytes);

            // Картинку показываем сразу из того, что выбрали: ждать, пока
            // сервер её примет и вернёт номер, незачем
            localMedia.put(local, bytes);
            JSONObject item = Net.frame("media", "nick", me.optString("name"),
                    "kind", "image", "name", name, "size", bytes.length,
                    "user", me.optInt("id"), "local", local, "at", stamp(),
                    "conversation", conversation);
            items.add(item);
            showItem(item);
        } catch (Exception error) {
            toast(Lang.t("Не удалось прочитать файл."));
        }
    }

    // ------------------------------------------------------------- профиль

    /** Экран настроек: своя страница, а не тесное окошко поверх списка. */
    private void openSettings() {
        // Прежний экран убираем: иначе они копились бы один поверх другого
        if (settingsScreen != null) {
            root.removeView(settingsScreen);
        }
        settingsScreen = buildSettings();
        root.addView(settingsScreen);
        show(settingsScreen);
    }

    private View buildSettings() {
        LinearLayout screen = Ui.column(this);
        screen.setBackgroundColor(Ui.CHAT_BG);

        LinearLayout bar = Ui.row(this);
        bar.setBackgroundColor(Ui.SIDEBAR);
        bar.setPadding(Ui.dp(this, 16), Ui.dp(this, 14), Ui.dp(this, 16),
                Ui.dp(this, 14));
        TextView back = Ui.text(this, "‹", 26, Ui.ACCENT);
        back.setPadding(0, 0, Ui.dp(this, 14), 0);
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                show(listScreen);
            }
        });
        bar.addView(back);
        bar.addView(Ui.text(this, Lang.t("Настройки"), 20, Ui.TEXT), Ui.grow());
        screen.addView(bar, Ui.wide());

        ScrollView scroll = new ScrollView(this);
        LinearLayout column = Ui.column(this);
        scroll.addView(column);

        // -------------------------------------------------- шапка с фото
        LinearLayout top = Ui.column(this);
        top.setGravity(Gravity.CENTER);
        top.setPadding(0, Ui.dp(this, 24), 0, Ui.dp(this, 20));

        String имя = me == null ? "" : me.optString("name");
        TextView лицо = Ui.avatar(this, имя, Ui.dp(this, 96));
        лицо.setTextSize(34);
        top.addView(лицо);
        if (me != null) {
            paintPhoto(лицо, me.optString("avatar", ""));
        }

        TextView сменить = Ui.text(this, Lang.t("Сменить фото"), 14, Ui.ACCENT);
        сменить.setPadding(0, Ui.dp(this, 10), 0, 0);
        сменить.setGravity(Gravity.CENTER);
        сменить.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                photoTarget = MY_AVATAR;
                pickPhoto();
            }
        });
        top.addView(сменить);

        TextView подпись = Ui.text(this, имя, 20, Ui.TEXT);
        подпись.setPadding(0, Ui.dp(this, 12), 0, 0);
        подпись.setGravity(Gravity.CENTER);
        top.addView(подпись);

        TextView ник = Ui.text(this,
                "@" + (me == null ? "" : me.optString("login")), 14, Ui.MUTED);
        ник.setGravity(Gravity.CENTER);
        top.addView(ник);
        column.addView(top, Ui.wide());

        // -------------------------------------------------- сами настройки
        column.addView(section(Lang.t("АККАУНТ")), Ui.wide());
        column.addView(settingsRow(Lang.t("Мой профиль"),
                Lang.t("Имя и пара слов о себе"), new Runnable() {
            @Override
            public void run() {
                showProfile();
            }
        }), Ui.wide());
        column.addView(section(Lang.t("ПРИЛОЖЕНИЕ")), Ui.wide());
        column.addView(settingsRow(Lang.t("Язык"),
                "ru".equals(Lang.current()) ? "Русский" : "English",
                new Runnable() {
            @Override
            public void run() {
                String next = "ru".equals(Lang.current()) ? "en" : "ru";
                prefs().edit().putString("language", next).apply();
                Lang.set(next);
                recreate();
            }
        }), Ui.wide());
        column.addView(settingsRow(Lang.t("Сервер"),
                prefs().getString("server", ""), null), Ui.wide());
        column.addView(settingsRow(Lang.t("Версия"), appVersion(), null),
                Ui.wide());

        // Обновление: показываем строчку, только если сервер раздаёт новее
        final String свежая = apkOffer == null ? "" : apkOffer.optString("version");
        if (isNewer(свежая, appVersion())) {
            TextView обновить = settingsRow(
                    Lang.t("Обновить до {version}", "version", свежая),
                    humanSize(apkOffer.optLong("size")), new Runnable() {
                @Override
                public void run() {
                    toast(Lang.t("Скачиваю обновление…"));
                    send(Net.frame("apk"));
                }
            });
            обновить.setTextColor(Ui.ACCENT);
            column.addView(обновить, Ui.wide());
        }

        column.addView(section(""), Ui.wide());
        TextView выйти = settingsRow(Lang.t("Выйти из аккаунта"), "", new Runnable() {
            @Override
            public void run() {
                signOut();
            }
        });
        выйти.setTextColor(Ui.DANGER);
        column.addView(выйти, Ui.wide());

        // Растягиваем по высоте, а не по ширине: Ui.grow() задаёт нулевую
        // ширину, и в вертикальной колонке содержимое просто пропало бы
        LinearLayout.LayoutParams растянуть = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0);
        растянуть.weight = 1;
        screen.addView(scroll, растянуть);
        return screen;
    }

    /** Заголовок раздела настроек. */
    private TextView section(String caption) {
        TextView заголовок = Ui.text(this, caption, 13, Ui.MUTED);
        заголовок.setPadding(Ui.dp(this, 16), Ui.dp(this, 18), Ui.dp(this, 16),
                Ui.dp(this, 6));
        return заголовок;
    }

    /** Строчка настроек: название сверху, пояснение снизу. */
    private TextView settingsRow(String title, String note, final Runnable дело) {
        TextView строка = Ui.text(this,
                note.isEmpty() ? title : title + "\n" + note, 16, Ui.TEXT);
        строка.setPadding(Ui.dp(this, 16), Ui.dp(this, 14), Ui.dp(this, 16),
                Ui.dp(this, 14));
        строка.setBackgroundColor(Ui.SIDEBAR);
        if (дело != null) {
            строка.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    дело.run();
                }
            });
        }
        return строка;
    }

    /**
     * Правда ли, что candidate новее current.
     *
     * Считаем как в version.py: ведущий ноль — украшение, а не число, иначе
     * 0.2.7.0 оказалось бы старше 2.6.0.
     */
    private static boolean isNewer(String candidate, String current) {
        int[] свежая = разобрать(candidate);
        int[] своя = разобрать(current);
        for (int место = 0; место < 3; место++) {
            if (свежая[место] != своя[место]) {
                return свежая[место] > своя[место];
            }
        }
        return false;
    }

    private static int[] разобрать(String версия) {
        List<Integer> части = new ArrayList<>();
        for (String кусок : String.valueOf(версия).split("\\.")) {
            StringBuilder цифры = new StringBuilder();
            for (char буква : кусок.toCharArray()) {
                if (Character.isDigit(буква)) {
                    цифры.append(буква);
                }
            }
            части.add(цифры.length() == 0 ? 0 : Integer.parseInt(цифры.toString()));
        }
        while (части.size() > 1 && части.get(0) == 0) {
            части.remove(0);
        }
        while (части.size() < 3) {
            части.add(0);
        }
        return new int[]{части.get(0), части.get(1), части.get(2)};
    }

    /**
     * Ставит присланное приложение.
     *
     * Через PackageInstaller, а не через «открыть файл»: своего
     * FileProvider у нас нет, а системе поток отдать можно и так. Спросить
     * человека она всё равно спросит — это её дело, не наше.
     */
    /**
     * Ставит скачанное обновление.
     *
     * Тут была тихая яма. Система не ставит приложение молча: собрав сессию,
     * она отвечает «нужно спросить человека» и присылает вместе с ответом
     * готовое окно с вопросом. Ответ приходит рассылкой — а слушать её было
     * некому, так что окно никто не показывал, и обновление вечно ждало
     * согласия, которого не у кого было спросить. Со стороны это выглядит
     * так, будто кнопка не работает.
     */
    private void installApk(byte[] данные) {
        if (данные == null || данные.length == 0) {
            toast(Lang.t("Обновление не установилось"));
            return;
        }

        слушатьУстановку();

        try {
            android.content.pm.PackageInstaller ставщик =
                    getPackageManager().getPackageInstaller();
            android.content.pm.PackageInstaller.SessionParams условия =
                    new android.content.pm.PackageInstaller.SessionParams(
                            android.content.pm.PackageInstaller.SessionParams
                                    .MODE_FULL_INSTALL);
            условия.setAppPackageName(getPackageName());
            int номер = ставщик.createSession(условия);
            android.content.pm.PackageInstaller.Session сессия =
                    ставщик.openSession(номер);
            java.io.OutputStream поток = сессия.openWrite("velix", 0, данные.length);
            поток.write(данные);
            сессия.fsync(поток);
            поток.close();

            android.app.PendingIntent ответ = android.app.PendingIntent.getBroadcast(
                    this, номер, new Intent(УСТАНОВЛЕНО).setPackage(getPackageName()),
                    android.app.PendingIntent.FLAG_MUTABLE
                            | android.app.PendingIntent.FLAG_UPDATE_CURRENT);
            сессия.commit(ответ.getIntentSender());
            сессия.close();
        } catch (Exception беда) {
            android.util.Log.e("Velix", "обновление не поставилось", беда);
            toast(Lang.t("Обновление не установилось"));
        }
    }

    private static final String УСТАНОВЛЕНО = "org.vexorter.velix.INSTALLED";
    private android.content.BroadcastReceiver установщикОтветил;

    /** Слушает, что скажет система об установке, и показывает её вопрос. */
    private void слушатьУстановку() {
        if (установщикОтветил != null) {
            return;
        }
        установщикОтветил = new android.content.BroadcastReceiver() {
            @Override
            public void onReceive(Context где, Intent что) {
                int как = что.getIntExtra(
                        android.content.pm.PackageInstaller.EXTRA_STATUS,
                        android.content.pm.PackageInstaller.STATUS_FAILURE);

                if (как == android.content.pm.PackageInstaller
                        .STATUS_PENDING_USER_ACTION) {
                    // Вот оно, то самое окно с вопросом «поставить?»
                    Intent спросить = что.getParcelableExtra(Intent.EXTRA_INTENT);
                    if (спросить != null) {
                        спросить.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        try {
                            startActivity(спросить);
                        } catch (Exception беда) {
                            toast(Lang.t("Обновление не установилось"));
                        }
                    }
                    return;
                }

                if (как == android.content.pm.PackageInstaller.STATUS_SUCCESS) {
                    toast(Lang.t("Обновление установлено"));
                    return;
                }

                String почему = что.getStringExtra(
                        android.content.pm.PackageInstaller.EXTRA_STATUS_MESSAGE);
                android.util.Log.e("Velix", "установка не прошла: " + как
                        + " " + почему);
                toast(Lang.t("Обновление не установилось")
                        + (почему == null ? "" : " " + почему));
            }
        };

        IntentFilter про = new IntentFilter(УСТАНОВЛЕНО);
        if (Build.VERSION.SDK_INT >= 33) {
            // С Android 13 рассылку без этого не примут: своя она или чужая,
            // система хочет знать наверняка
            registerReceiver(установщикОтветил, про, RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(установщикОтветил, про);
        }
    }

    /** Номер сборки — его знает сама система, дублировать незачем. */
    private String appVersion() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0)
                    .versionName;
        } catch (Exception error) {
            return "";
        }
    }

    private void signOut() {
        send(Net.frame("logout"));
        prefs().edit().remove("token").apply();
        if (service != null) {
            service.shutdown();
            unbindService(connection);
            service = null;
            bound = false;
        }
        show(authScreen);
    }

    private void showProfile() {
        LinearLayout card = Ui.column(this);
        card.setPadding(Ui.dp(this, 20), Ui.dp(this, 12), Ui.dp(this, 20), 0);

        final EditText name = Ui.field(this, Lang.t("Как вас зовут"));
        name.setText(me.optString("name"));
        card.addView(name, Ui.wide());

        final EditText bio = Ui.field(this, Lang.t("Пара слов о себе"));
        bio.setText(me.optString("bio"));
        card.addView(bio, Ui.wide());

        new AlertDialog.Builder(this)
                .setTitle(Lang.t("Профиль"))
                .setView(card)
                .setPositiveButton(Lang.t("Сохранить"),
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                send(Net.frame("profile", "name",
                                        name.getText().toString().trim(),
                                        "bio", bio.getText().toString().trim()));
                            }
                        })
                .setNegativeButton(Lang.t("Отмена"), null)
                .show();
    }

    // --------------------------------------------------------- кадры сервера

    @Override
    public void onOpen(boolean secure) {
        if (!pendingSignIn) {
            return;    // служба сама войдёт по сохранённому токену
        }
        pendingSignIn = false;

        String token = prefs().getString("token", null);
        if (recoverMode) {
            send(Net.frame("recover",
                    "login", loginField.getText().toString().trim(),
                    "code", codeField.getText().toString().trim(),
                    "password", passwordField.getText().toString()));
            return;
        }
        if (token != null && !registerMode) {
            send(Net.frame("auth", "token", token));
            return;
        }
        if (registerMode) {
            send(Net.frame("register",
                    "login", loginField.getText().toString().trim(),
                    "password", passwordField.getText().toString(),
                    "name", nameField.getText().toString().trim(),
                    "invite", inviteField.getText().toString().trim()));
        } else {
            send(Net.frame("login",
                    "login", loginField.getText().toString().trim(),
                    "password", passwordField.getText().toString()));
        }
    }

    @Override
    public void onFrame(JSONObject frame) {
        String kind = frame.optString("type");

        if ("welcome".equals(kind)) {
            me = frame.optJSONObject("user");
            prefs().edit().putString("token", frame.optString("token")).apply();
            loadDrafts();
            flushOutbox();
            apkOffer = frame.optJSONObject("apk");
            if (frame.optJSONObject("limits") != null) {
                limits = frame.optJSONObject("limits");
            }

            // Приветствие из запаса службы только напоминает, кто мы: ни
            // списки чистить, ни уводить человека из переписки не нужно
            if (frame.optBoolean("cached")) {
                return;
            }

            if (recoverMode) {
                recoverMode = false;
                drawAuthMode();
            }
            String recovery = frame.optString("recovery", "");
            if (!recovery.isEmpty()) {
                showRecovery(recovery);
            }
            conversations.clear();
            people.clear();
            show(listScreen);

        } else if ("authfail".equals(kind)) {
            prefs().edit().remove("token").apply();
            authError.setText(Lang.fromServer(frame));
            authSubtitle.setText(registerMode ? Lang.t("Нужен код приглашения")
                                              : Lang.t("Вход в аккаунт"));
            show(authScreen);

        } else if ("error".equals(kind)) {
            toast(Lang.fromServer(frame));

        } else if ("conversations".equals(kind)) {
            fromCache = false;
            keepRooms(frame.optJSONArray("items"));
            conversations.clear();
            JSONArray list = frame.optJSONArray("items");
            for (int index = 0; list != null && index < list.length(); index++) {
                conversations.add(list.optJSONObject(index));
            }
            drawList();

            if (pendingDirect > 0) {
                for (JSONObject item : conversations) {
                    if ("direct".equals(item.optString("kind"))
                            && item.optInt("user") == pendingDirect) {
                        pendingDirect = -1;
                        openConversation(item.optInt("id"));
                        break;
                    }
                }
            } else if (conversation < 0) {
                // Экран мог пересоздаться: возвращаем человека туда, где он был
                int был = prefs().getInt("open", -1);
                for (JSONObject item : conversations) {
                    if (был > 0 && item.optInt("id") == был) {
                        openConversation(был);
                        break;
                    }
                }
            }

            if (pendingUpload != null && service != null) {
                // Файл выбрали, пока приложение перезапускалось
                Uri что = pendingUpload;
                pendingUpload = null;
                sendBigFile(что);
            }

        } else if ("conversation".equals(kind)) {
            JSONObject item = frame.optJSONObject("item");
            if (item != null) {
                // Та же переписка могла прийти обновлённой — например, с фото
                for (int index = conversations.size() - 1; index >= 0; index--) {
                    if (conversations.get(index).optInt("id") == item.optInt("id")) {
                        conversations.remove(index);
                    }
                }
                conversations.add(item);
                drawList();
                if (pendingGroup) {
                    pendingGroup = false;
                    openConversation(item.optInt("id"));
                }
            }

        } else if ("people".equals(kind)) {
            people.clear();
            JSONArray list = frame.optJSONArray("items");
            for (int index = 0; list != null && index < list.length(); index++) {
                people.add(list.optJSONObject(index));
            }
            online.clear();
            JSONArray active = frame.optJSONArray("online");
            for (int index = 0; active != null && index < active.length(); index++) {
                online.add(active.optInt(index));
            }
            for (JSONObject person : people) {
                String когда = person.optString("seen", "");
                if (!когда.isEmpty()) {
                    seen.put(person.optInt("id"), когда);
                }
            }
            drawList();
            updateChatStatus();

        } else if ("presence".equals(kind)) {
            if (frame.optBoolean("online")) {
                online.add(frame.optInt("user"));
            } else {
                online.remove(frame.optInt("user"));
                String когда = frame.optString("seen", "");
                if (!когда.isEmpty()) {
                    seen.put(frame.optInt("user"), когда);
                }
            }
            drawList();
            updateChatStatus();

        } else if ("history".equals(kind)) {
            showHistory(frame);

        } else if ("text".equals(kind) || "media".equals(kind)) {
            if (frame.optInt("conversation", conversation) == conversation) {
                items.add(frame);
                showItem(frame);
                markRead();
            } else {
                // Пришло в другую переписку: показываем это в списке —
                // и строчкой снизу, и красным кружком
                rememberLast(frame);
                drawList();
            }

        } else if ("upload_ready".equals(kind)) {
            Object[] отправка = pendingUploads.get(frame.optString("local"));
            if (отправка != null) {
                pushChunks(frame.optString("ticket"), (Uri) отправка[0],
                        frame.optInt("chunk", 4 * 1024 * 1024));
            }

        } else if ("upload_progress".equals(kind)) {
            if (uploadLine != null) {
                long ушло = frame.optLong("sent");
                long всего = Math.max(frame.optLong("size"), 1);
                uploadLine.setText(Lang.t("Отправляю «{name}» — {percent}%",
                        "name", uploadName(), "percent",
                        String.valueOf(ушло * 100 / всего)));
            }

        } else if ("ack".equals(kind)) {
            onAck(frame);

        } else if ("receipts".equals(kind)) {
            JSONObject list = frame.optJSONObject("items");
            java.util.Iterator<String> keys = list == null
                    ? new ArrayList<String>().iterator() : list.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                paintTick(Integer.parseInt(key), list.optString(key));
            }

        } else if ("reactions".equals(kind)) {
            reactions.put(frame.optInt("id"), frame.optJSONObject("reactions"));
            drawReactions(frame.optInt("id"));

        } else if ("deleted".equals(kind)) {
            openConversation(conversation);   // проще перерисовать ленту целиком

        } else if ("pinned".equals(kind)) {
            String key = String.valueOf(frame.optInt("conversation"));
            JSONObject item = frame.optJSONObject("item");
            if (item == null) {
                pinned.remove(key);
            } else {
                pinned.put(key, item);
            }
            refreshPinBar();

        } else if ("profile".equals(kind)) {
            me = frame.optJSONObject("user");
            toast(Lang.t("Сохранено"));

            // Экран настроек показывает и фото, и имя — пересобираем его
            if (settingsScreen != null && settingsScreen.getVisibility()
                    == View.VISIBLE) {
                openSettings();
            }

        } else if ("gallery".equals(kind)) {
            if (frame.optInt("conversation") == conversation) {
                showGallery(frame.optJSONArray("items"));
            }

        } else if ("preview".equals(kind)) {
            if (frame.optInt("conversation") == conversation) {
                JSONObject карточка = new JSONObject();
                try {
                    for (String ключ : new String[]{"url", "title", "text",
                                                    "site", "image"}) {
                        if (frame.has(ключ)) {
                            карточка.put(ключ, frame.get(ключ));
                        }
                    }
                    for (JSONObject one : items) {
                        if (one.optInt("id") == frame.optInt("id")) {
                            one.put("preview", карточка);
                        }
                    }
                } catch (Exception ignored) {
                    // Кадр пришёл странный — карточку просто не покажем
                }
                // Карточка приезжает после истории — сохранённое обновляем,
                // иначе без сети от ссылки остался бы голый адрес
                keepHistory(conversation, items);

                LinearLayout пузырь = bubbles.get(frame.optInt("id"));
                if (пузырь != null) {
                    linkCard(пузырь, карточка);
                    scrollDown();
                }
            }

        } else if ("edited".equals(kind)) {
            int номер = frame.optInt("id");
            for (JSONObject one : items) {
                if (one.optInt("id") == номер) {
                    try {
                        one.put("text", frame.optString("text"));
                        one.put("edited", frame.optString("edited"));
                    } catch (Exception ignored) {
                        // Не сложилось — перерисовка всё равно покажет своё
                    }
                    break;
                }
            }
            if (frame.optInt("conversation") == conversation) {
                openConversation(conversation);   // проще перерисовать целиком
            }

        } else if ("typing".equals(kind)) {
            if (frame.optInt("conversation") == conversation) {
                typingWho = frame.optString("nick");
                typingUntil = System.currentTimeMillis() + 3000;
                updateChatStatus();
                chatStatus.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        if (System.currentTimeMillis() >= typingUntil) {
                            typingWho = null;
                            updateChatStatus();
                        }
                    }
                }, 3200);
            }
        }
    }

    /** Имя того, что сейчас отправляется — для строчки о ходе. */
    private String uploadName() {
        for (Object[] один : pendingUploads.values()) {
            return String.valueOf(один[1]);
        }
        return "";
    }

    private void onAck(JSONObject frame) {
        String local = frame.optString("local");
        int id = frame.optInt("id");

        Object[] отправка = pendingUploads.remove(local);
        if (отправка != null) {
            // Большое вложение доехало: убираем строчку о ходе и показываем его
            if (uploadLine != null) {
                feed.removeView(uploadLine);
                uploadLine = null;
            }
            JSONObject item = Net.frame("media", "nick", me.optString("name"),
                    "kind", kindOf(String.valueOf(отправка[1])),
                    "name", String.valueOf(отправка[1]),
                    "size", отправка[2], "user", me.optInt("id"),
                    "id", id, "media", frame.optString("media"),
                    "at", frame.optString("at"), "conversation", conversation);
            items.add(item);
            showItem(item);
            return;
        }
        for (JSONObject item : items) {
            if (local.equals(item.optString("local"))) {
                try {
                    item.put("id", id);
                } catch (Exception ignored) {
                    // Номер не приложился — галочки просто не обновятся
                }
            }
        }
        TextView mark = pendingTicks.remove(local);
        if (mark != null) {
            ticks.put(id, mark);
        }
        LinearLayout marks = pendingMarks.remove(local);
        if (marks != null) {
            reactionRows.put(id, marks);
        }

        // Вложение получило номер: запоминаем содержимое под ним, чтобы
        // картинка осталась на месте и после перерисовки
        String mediaId = frame.optString("media", "");
        if (!mediaId.isEmpty()) {
            byte[] bytes = localMedia.remove(local);
            if (bytes != null) {
                media.put(mediaId, bytes);
            }
            for (JSONObject item : items) {
                if (local.equals(item.optString("local"))) {
                    try {
                        item.put("media", mediaId);
                    } catch (Exception ignored) {
                        // Номер не приложился — картинка просто перезапросится
                    }
                }
            }
        }
        paintTick(id, states.containsKey(id) ? states.get(id) : "sent");
    }

    private void showHistory(JSONObject frame) {
        if (frame.optInt("conversation") != conversation) {
            return;
        }

        JSONObject quoted = frame.optJSONObject("quotes");
        if (quoted != null) {
            java.util.Iterator<String> keys = quoted.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                quotes.put(Integer.parseInt(key), quoted.optJSONObject(key));
            }
        }

        JSONObject marks = frame.optJSONObject("reactions");
        if (marks != null) {
            java.util.Iterator<String> keys = marks.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                reactions.put(Integer.parseInt(key), marks.optJSONObject(key));
            }
        }

        feed.removeAllViews();
        items.clear();
        ticks.clear();
        bubbles.clear();
        stopVoice();
        stopCircle();
        reactionRows.clear();
        currentDate = null;
        emptyHint = null;

        JSONArray list = frame.optJSONArray("items");
        drawingHistory = true;
        try {
            for (int index = 0; list != null && index < list.length(); index++) {
                JSONObject item = list.optJSONObject(index);
                items.add(item);
                showItem(item);
            }
        } finally {
            drawingHistory = false;
        }

        keepHistory(conversation, items);

        if (items.isEmpty()) {
            TextView hint = Ui.text(this, Lang.t("Пока тихо. Напишите первым."),
                    13, Ui.MUTED);
            hint.setGravity(Gravity.CENTER);
            hint.setPadding(0, Ui.dp(this, 20), 0, 0);
            feed.addView(hint, Ui.wide());
            emptyHint = hint;
        }
        markRead();
    }

    private void refreshPinBar() {
        JSONObject item = pinned.get(String.valueOf(conversation));
        if (item == null) {
            pinBar.setVisibility(View.GONE);
            return;
        }
        String what = "text".equals(item.optString("kind", "text"))
                ? item.optString("text") : item.optString("name");
        pinLabel.setText(item.optString("nick") + ": " + cut(what, 50));
        pinBar.setVisibility(View.VISIBLE);
    }

    @Override
    public void onBlob(JSONObject header, byte[] data) {
        String id = header.optString("id");

        // Приложение для телефона приезжает тем же путём, что и вложение
        if ("apk_blob".equals(header.optString("type"))) {
            String файл = header.optString("file", "");
            byte[] байты = data;
            if (!файл.isEmpty()) {
                try {
                    java.io.File лежит = new java.io.File(файл);
                    byte[] буфер = new byte[(int) лежит.length()];
                    java.io.FileInputStream поток = new java.io.FileInputStream(лежит);
                    int прочитано = 0;
                    while (прочитано < буфер.length) {
                        int шаг = поток.read(буфер, прочитано, буфер.length - прочитано);
                        if (шаг <= 0) {
                            break;
                        }
                        прочитано += шаг;
                    }
                    поток.close();
                    лежит.delete();
                    байты = буфер;
                } catch (Exception ignored) {
                    байты = null;
                }
            }
            installApk(байты);
            return;
        }

        // Большое вложение приехало файлом: в памяти его не держим
        String путь = header.optString("file", "");
        if (!путь.isEmpty()) {
            java.io.File готовое = new java.io.File(путь);
            if (waitingVideos.remove(id) != null) {
                // Ролик оставляем на диске: с него его и проиграем
                videoFiles.put(id, готовое.getAbsolutePath());
                if (id.equals(viewerWaiting) && viewerRepaint != null) {
                    viewerWaiting = "";
                    viewerRepaint.run();
                }
                return;
            }
            String имя = waitingFiles.remove(id);
            if (имя != null) {
                storeInDownloads(готовое, имя);
            } else {
                готовое.delete();
            }
            return;
        }

        media.put(id, data);
        Runnable ждало = pendingPlay.remove(id);
        if (ждало != null) {
            ждало.run();
        }
        if (waitingVideos.remove(id) != null) {
            keepVideo(id, data);
        }
        if (id.equals(viewerWaiting) && viewerRepaint != null) {
            viewerWaiting = "";
            viewerRepaint.run();
        }

        List<TextView> faces = photoSlots.remove(id);
        if (faces != null) {
            Bitmap round = circleBitmap(data);
            for (TextView face : faces) {
                if (!id.equals(face.getTag())) {
                    continue;     // кружок за это время достался другому
                }
                face.setText("");
                face.setBackground(new android.graphics.drawable.BitmapDrawable(
                        getResources(), round));
            }
        }

        List<ImageView> slots = waiting.remove(id);
        if (slots == null) {
            return;
        }
        Bitmap picture = BitmapFactory.decodeByteArray(data, 0, data.length);
        for (ImageView slot : slots) {
            slot.setImageBitmap(picture);
        }
        scrollDown();
    }

    @Override
    public void onClosed(String reason) {
        // Переподключением занимается служба, экран просто говорит об этом
        chatStatus.setText(Lang.t("нет связи"));
    }

    @Override
    public void onBackPressed() {
        if (chatScreen.getVisibility() == View.VISIBLE) {
            leaveConversation();
            return;
        }
        super.onBackPressed();
    }

    /** Человек ушёл из переписки сам — возвращать его туда не надо. */
    private void leaveConversation() {
        conversation = -1;
        if (service != null) {
            service.rememberOpen(-1);
        }
        show(listScreen);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // Соединение остаётся жить в службе: она и уведомления показывает
        if (bound) {
            unbindService(connection);
            bound = false;
        }
    }
}
