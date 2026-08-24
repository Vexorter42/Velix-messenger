package org.vexorter.velix;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.util.Log;
import android.webkit.ConsoleMessage;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

/**
 * Velix для Android.
 *
 * Приложение показывает тот же веб-клиент, что сервер раздаёт по своему
 * адресу. Отдельного кода чата тут нет намеренно: одна разметка на телефоне
 * и в браузере означает, что новая возможность появляется сразу везде.
 *
 * Адрес сервера спрашивается при первом запуске и запоминается. Если по нему
 * не достучаться, спросим снова — заодно это чинит опечатку в адресе.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "velix";
    private static final String KEY_SERVER = "server";
    private static final String DEFAULT_SERVER = "velix.vexorter.duckdns.org:8765";
    private static final int PICK_FILE = 1;

    private WebView web;
    private ValueCallback<Uri[]> chooser;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        web = new WebView(this);
        setContentView(web);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        // Без этого не сохранится ни вход, ни выбранный язык
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUserAgentString(settings.getUserAgentString() + " VelixApp");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request.isForMainFrame()) {
                    askServer(true);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage message) {
                // Без этого ошибки страницы не видно ни в logcat, ни где-либо
                Log.d("Velix", message.message() + " (" + message.sourceId()
                        + ":" + message.lineNumber() + ")");
                return true;
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (chooser != null) {
                    chooser.onReceiveValue(null);
                }
                chooser = callback;
                try {
                    startActivityForResult(params.createIntent(), PICK_FILE);
                } catch (Exception problem) {
                    chooser = null;
                    return false;
                }
                return true;
            }
        });

        String server = prefs().getString(KEY_SERVER, null);
        if (server == null) {
            askServer(false);
        } else {
            load(server);
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    /** Спрашивает адрес сервера и запоминает его. */
    private void askServer(boolean failed) {
        final EditText field = new EditText(this);
        field.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        field.setText(prefs().getString(KEY_SERVER, DEFAULT_SERVER));
        field.setSelectAllOnFocus(true);

        new AlertDialog.Builder(this)
                .setTitle(failed ? R.string.server_failed : R.string.server_title)
                .setView(field)
                .setCancelable(false)
                .setPositiveButton(R.string.connect, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        String server = field.getText().toString().trim();
                        if (server.isEmpty()) {
                            server = DEFAULT_SERVER;
                        }
                        prefs().edit().putString(KEY_SERVER, server).apply();
                        load(server);
                    }
                })
                .show();
    }

    /** Открывает веб-клиент. Без схемы считаем адрес защищённым. */
    private void load(String server) {
        String address = server.trim();
        if (!address.startsWith("http://") && !address.startsWith("https://")) {
            address = "https://" + address;
        }
        web.loadUrl(address);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request != PICK_FILE) {
            super.onActivityResult(request, result, data);
            return;
        }
        if (chooser == null) {
            return;
        }
        chooser.onReceiveValue(WebChromeClient.FileChooserParams
                .parseResult(result, data));
        chooser = null;
    }

    /**
     * Кнопка «назад» ведёт по экранам чата, а не сразу из приложения:
     * об этом спрашиваем сам веб-клиент. Ответ приходит не сразу, поэтому
     * выход откладываем до него.
     */
    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.velixBack ? window.velixBack() : false",
                new ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String handled) {
                        if (!"true".equals(handled)) {
                            finish();
                        }
                    }
                });
    }
}
