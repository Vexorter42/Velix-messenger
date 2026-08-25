package org.vexorter.velix;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.ComponentName;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
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
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
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
        authScreen.setVisibility(screen == authScreen ? View.VISIBLE : View.GONE);
        listScreen.setVisibility(screen == listScreen ? View.VISIBLE : View.GONE);
        chatScreen.setVisibility(screen == chatScreen ? View.VISIBLE : View.GONE);
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
            String what = "text".equals(last.optString("kind"))
                    ? last.optString("text") : Lang.t("вложение");
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

    // Куда идёт выбранная фотография: 0 — своя аватарка, иначе фото группы
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
        composer.addView(messageField, Ui.grow());

        TextView send = Ui.text(this, "➤", 20, Ui.ACCENT);
        send.setPadding(Ui.dp(this, 12), 0, Ui.dp(this, 6), 0);
        send.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                sendText();
            }
        });
        composer.addView(send);
        screen.addView(composer, Ui.wide());

        return screen;
    }

    private void openConversation(int id) {
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
        reactionRows.clear();

        JSONObject item = conversationById(id);
        String title = item == null ? "Velix" : titleOf(item);
        chatTitle.setText(title);
        chatAvatar.setText(Ui.initial(title));
        chatAvatar.setBackground(Ui.circle(Ui.avatarColor(title)));
        if (item != null) {
            paintPhoto(chatAvatar, item.optString("avatar", ""));
        }
        refreshPinBar();

        send(Net.frame("open", "conversation", id));
        show(chatScreen);
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
        send(frame);

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
        bubble.setBackground(Ui.rounded(own ? Ui.BUBBLE_OUT : Ui.BUBBLE_IN,
                Ui.dp(this, 14)));
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
            quote.setBackground(Ui.rounded(Ui.SEPARATOR, Ui.dp(this, 8)));
            quote.setPadding(Ui.dp(this, 8), Ui.dp(this, 4), Ui.dp(this, 8),
                    Ui.dp(this, 4));
            bubble.addView(quote, Ui.wide());
        }

        String kind = item.optString("kind", "text");
        if ("text".equals(kind)) {
            TextView body = Ui.text(this, item.optString("text"), 16, Ui.TEXT);
            bubble.addView(body, Ui.wide());
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
        scrollDown();
    }

    private final Map<String, TextView> pendingTicks = new HashMap<>();
    private final Map<String, LinearLayout> pendingMarks = new HashMap<>();
    private View emptyHint;

    private View attachment(final JSONObject item) {
        String kind = item.optString("kind");
        if (!"image".equals(kind) && !"gif".equals(kind)) {
            String подпись = "video".equals(kind) ? Lang.t("Видео") : Lang.t("Файл");
            long вес = item.optLong("size");
            TextView card = Ui.text(this, подпись + " · "
                    + item.optString("name")
                    + (вес > 0 ? "\n" + humanSize(вес) : ""), 15, Ui.TEXT);
            card.setPadding(0, Ui.dp(this, 4), 0, Ui.dp(this, 4));

            final String id = item.optString("media", "");
            final String имя = item.optString("name", "файл");
            if (!id.isEmpty()) {
                card.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View view) {
                        saveAttachment(id, имя);
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
                if (bytes != null) {
                    showFull(bytes);
                }
            }
        });
        return picture;
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

    private void messageMenu(final JSONObject item) {
        final Dialog dialog = new Dialog(this);
        dialog.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE);

        LinearLayout card = Ui.column(this);
        card.setBackground(Ui.rounded(Ui.SIDEBAR, Ui.dp(this, 14)));
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
        replyBar.setVisibility(View.GONE);
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
    private void showFull(byte[] data) {
        final Dialog dialog = new Dialog(this,
                android.R.style.Theme_Black_NoTitleBar_Fullscreen);
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
                холст.postTranslate(-сдвигX, -сдвигY);
                picture.setImageMatrix(холст);
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

        dialog.setContentView(picture);
        dialog.show();
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
            mark.setBackground(Ui.rounded(Ui.INPUT_BG, Ui.dp(this, 10)));
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
                photoTarget = 0;      // ноль — своя аватарка, не группа
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
        column.addView(settingsRow(Lang.t("Фото профиля"),
                Lang.t("Кружок, который видят остальные"), new Runnable() {
            @Override
            public void run() {
                photoTarget = 0;
                pickPhoto();
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
            drawList();

        } else if ("presence".equals(kind)) {
            if (frame.optBoolean("online")) {
                online.add(frame.optInt("user"));
            } else {
                online.remove(frame.optInt("user"));
            }
            drawList();

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

        } else if ("typing".equals(kind)) {
            if (frame.optInt("conversation") == conversation) {
                chatStatus.setText(Lang.t("{name} печатает…", "name",
                        frame.optString("nick")));
                chatStatus.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        chatStatus.setText("");
                    }
                }, 3000);
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
        reactionRows.clear();
        currentDate = null;
        emptyHint = null;

        JSONArray list = frame.optJSONArray("items");
        for (int index = 0; list != null && index < list.length(); index++) {
            JSONObject item = list.optJSONObject(index);
            items.add(item);
            showItem(item);
        }

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

        // Большое вложение приехало файлом: в памяти его не держим
        String путь = header.optString("file", "");
        if (!путь.isEmpty()) {
            java.io.File готовое = new java.io.File(путь);
            String имя = waitingFiles.remove(id);
            if (имя != null) {
                storeInDownloads(готовое, имя);
            } else {
                готовое.delete();
            }
            return;
        }

        media.put(id, data);

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
