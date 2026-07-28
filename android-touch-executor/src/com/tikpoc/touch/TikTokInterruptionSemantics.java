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

    public static boolean containsExactVisibleText(
            SemanticSnapshot snapshot, String expected) {
        String normalized = expected == null
                ? "" : expected.trim().toLowerCase(Locale.ROOT);
        if (normalized.isEmpty()) return false;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()) continue;
            if (node.searchableText().trim().equals(normalized)) return true;
        }
        return false;
    }

    public static boolean hasVisibleVideoIdentity(
            SemanticSnapshot snapshot, String creatorUsername, String captionAnchor) {
        String creator = normalizeIdentity(creatorUsername);
        String anchor = normalizePhrase(captionAnchor);
        if (creator.isEmpty() || anchor.isEmpty()) return false;
        boolean creatorVisible = false;
        boolean captionVisible = false;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()) continue;
            String visible = normalizePhrase(node.searchableText());
            if (containsCreatorToken(visible, creator)) creatorVisible = true;
            if (visible.contains(anchor)) captionVisible = true;
        }
        return creatorVisible && captionVisible;
    }

    private static String normalizeIdentity(String value) {
        String normalized = normalizePhrase(value);
        return normalized.startsWith("@") ? normalized.substring(1) : normalized;
    }

    private static boolean containsCreatorToken(String visible, String creator) {
        int from = 0;
        while (from <= visible.length() - creator.length()) {
            int index = visible.indexOf(creator, from);
            if (index < 0) return false;
            int beforeIndex = index - 1;
            int afterIndex = index + creator.length();
            boolean before = beforeIndex < 0 || visible.charAt(beforeIndex) == '@'
                    || !isUsernameCharacter(visible.charAt(beforeIndex));
            boolean after = afterIndex >= visible.length()
                    || !isUsernameCharacter(visible.charAt(afterIndex));
            if (before && after) return true;
            from = index + 1;
        }
        return false;
    }

    private static boolean isUsernameCharacter(char value) {
        return Character.isLetterOrDigit(value) || value == '.' || value == '_';
    }

    private static String normalizePhrase(String value) {
        return value == null ? "" : value.trim().replaceAll("\\s+", " ")
                .toLowerCase(Locale.ROOT);
    }

    private static boolean containsAny(String value, String... phrases) {
        for (String phrase : phrases) {
            if (value.contains(phrase)) return true;
        }
        return false;
    }
}
