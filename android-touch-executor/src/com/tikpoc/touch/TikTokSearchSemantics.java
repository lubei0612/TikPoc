package com.tikpoc.touch;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public final class TikTokSearchSemantics {
    private TikTokSearchSemantics() {}

    public static String normalizeUsername(String value) {
        String normalized = Normalizer.normalize(value == null ? "" : value, Normalizer.Form.NFKC);
        StringBuilder visible = new StringBuilder();
        for (int index = 0; index < normalized.length();) {
            int codePoint = normalized.codePointAt(index);
            if (Character.getType(codePoint) != Character.FORMAT) visible.appendCodePoint(codePoint);
            index += Character.charCount(codePoint);
        }
        String result = visible.toString().trim();
        while (result.startsWith("@")) result = result.substring(1).trim();
        return result.toLowerCase(Locale.ROOT);
    }

    public static int uniqueExactIndex(String expected, List<String> candidates) {
        String wanted = normalizeUsername(expected);
        List<Integer> matches = new ArrayList<Integer>();
        for (int index = 0; index < candidates.size(); index++) {
            if (normalizeUsername(candidates.get(index)).equals(wanted)) matches.add(index);
        }
        if (matches.isEmpty()) return -1;
        if (matches.size() > 1) return -2;
        return matches.get(0);
    }
}
