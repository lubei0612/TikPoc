package com.tikpoc.touch;

import java.util.Locale;

public final class TikTokInterruptionSemantics {
    public static final String NONE = "none";
    public static final String ORDINARY_DIALOG = "ordinary_dialog";
    public static final String LONG_PRESS_MENU = "long_press_menu";
    public static final String VERIFICATION_REQUIRED = "verification_required";

    private TikTokInterruptionSemantics() {}

    public static String classify(SemanticSnapshot snapshot) {
        boolean ordinaryDialog = false;
        boolean longPressMenu = false;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()) continue;
            String text = node.searchableText().toLowerCase(Locale.ROOT);
            if (containsAny(text,
                    "请完成下列验证后继续", "verify to continue",
                    "complete the following verification")) {
                return VERIFICATION_REQUIRED;
            }
            if (containsAny(text,
                    "与好友一起使用 tiktok 会更有趣",
                    "tiktok is more fun with friends")) {
                ordinaryDialog = true;
            }
            if (containsAny(text,
                    "为什么看到此作品", "why you're seeing this post",
                    "why am i seeing this post")) {
                longPressMenu = true;
            }
        }
        if (longPressMenu) return LONG_PRESS_MENU;
        if (ordinaryDialog) return ORDINARY_DIALOG;
        return NONE;
    }

    public static boolean isHomeVisible(SemanticSnapshot snapshot) {
        boolean home = false;
        boolean feed = false;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()) continue;
            String text = node.searchableText().trim().toLowerCase(Locale.ROOT);
            if (text.equals("首页") || text.equals("home")) home = true;
            if (text.equals("推荐") || text.equals("for you")) feed = true;
        }
        return home && feed;
    }

    private static boolean containsAny(String value, String... phrases) {
        for (String phrase : phrases) {
            if (value.contains(phrase)) return true;
        }
        return false;
    }
}
