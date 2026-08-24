package org.vexorter.velix;

import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Мелочи оформления: цвета, отступы, скруглённые подложки.
 *
 * Разметка собирается кодом, а не XML: экранов немного, зато не приходится
 * держать в голове полтора десятка файлов ресурсов и их идентификаторы.
 */
class Ui {

    static final int SIDEBAR = Color.parseColor("#17212b");
    static final int CHAT_BG = Color.parseColor("#0e1621");
    static final int BUBBLE_IN = Color.parseColor("#182533");
    static final int BUBBLE_OUT = Color.parseColor("#2b5278");
    static final int INPUT_BG = Color.parseColor("#242f3d");
    static final int SEPARATOR = Color.parseColor("#1b2836");
    static final int TEXT = Color.parseColor("#ffffff");
    static final int MUTED = Color.parseColor("#708499");
    static final int ACCENT = Color.parseColor("#5288c1");
    static final int DANGER = Color.parseColor("#ec5f75");
    static final int ONLINE = Color.parseColor("#4dc866");
    static final int TICK = Color.parseColor("#7da8d3");
    static final int TICK_READ = Color.parseColor("#7ee2ff");

    static final int[] AVATAR_COLORS = {
            Color.parseColor("#e17076"), Color.parseColor("#faa774"),
            Color.parseColor("#a695e7"), Color.parseColor("#7bc862"),
            Color.parseColor("#6ec9cb"), Color.parseColor("#65aadd"),
            Color.parseColor("#ee7aae")};

    static int dp(Context context, float value) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP,
                value, context.getResources().getDisplayMetrics()));
    }

    /** Цвет аватарки закреплён за именем, чтобы не прыгал между запусками. */
    static int avatarColor(String name) {
        int sum = 0;
        for (char letter : (name == null || name.isEmpty() ? "?" : name).toCharArray()) {
            sum += letter;
        }
        return AVATAR_COLORS[Math.abs(sum) % AVATAR_COLORS.length];
    }

    static String initial(String name) {
        String value = name == null ? "" : name.trim();
        return value.isEmpty() ? "?" : value.substring(0, 1).toUpperCase();
    }

    static GradientDrawable rounded(int colour, int radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(colour);
        shape.setCornerRadius(radius);
        return shape;
    }

    static GradientDrawable circle(int colour) {
        GradientDrawable shape = new GradientDrawable();
        shape.setShape(GradientDrawable.OVAL);
        shape.setColor(colour);
        return shape;
    }

    /** Кружок с буквой — заглушка вместо фотографии. */
    static TextView avatar(Context context, String name, int side) {
        TextView view = new TextView(context);
        view.setText(initial(name));
        view.setTextColor(TEXT);
        view.setGravity(Gravity.CENTER);
        view.setTextSize(side / 2.6f / context.getResources().getDisplayMetrics().density);
        view.setBackground(circle(avatarColor(name)));
        view.setLayoutParams(new LinearLayout.LayoutParams(side, side));
        return view;
    }

    static TextView text(Context context, String value, int size, int colour) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        return view;
    }

    static EditText field(Context context, String hint) {
        EditText view = new EditText(context);
        view.setHint(hint);
        view.setHintTextColor(MUTED);
        view.setTextColor(TEXT);
        view.setTextSize(16);
        view.setBackground(rounded(INPUT_BG, dp(context, 10)));
        view.setPadding(dp(context, 14), dp(context, 12), dp(context, 14),
                dp(context, 12));
        return view;
    }

    static TextView button(Context context, String label, int colour, int textColour) {
        TextView view = new TextView(context);
        view.setText(label);
        view.setTextSize(16);
        view.setTextColor(textColour);
        view.setGravity(Gravity.CENTER);
        view.setBackground(rounded(colour, dp(context, 10)));
        view.setPadding(dp(context, 16), dp(context, 14), dp(context, 16),
                dp(context, 14));
        view.setClickable(true);
        return view;
    }

    static LinearLayout column(Context context) {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    static LinearLayout row(Context context) {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        return layout;
    }

    static LinearLayout.LayoutParams wide() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    static LinearLayout.LayoutParams grow() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.weight = 1;
        return params;
    }

    static void margins(View view, int left, int top, int right, int bottom) {
        ViewGroup.LayoutParams params = view.getLayoutParams();
        LinearLayout.LayoutParams layout = params instanceof LinearLayout.LayoutParams
                ? (LinearLayout.LayoutParams) params : wide();
        layout.setMargins(left, top, right, bottom);
        view.setLayoutParams(layout);
    }
}
