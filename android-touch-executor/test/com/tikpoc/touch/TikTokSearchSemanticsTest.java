package com.tikpoc.touch;

import java.util.Arrays;

public final class TikTokSearchSemanticsTest {
    public static void main(String[] args) {
        check(TikTokSearchSemantics.normalizeUsername(" \u200B@Target.User ")
                .equals("target.user"), "normalization");
        check(TikTokSearchSemantics.uniqueExactIndex("target_user",
                Arrays.asList("Target User", "@TARGET_USER", "target_user2")) == 1,
                "one exact username");
        check(TikTokSearchSemantics.uniqueExactIndex("target_user",
                Arrays.asList("Target User", "target_user2")) == -1, "no nickname match");
        check(TikTokSearchSemantics.uniqueExactIndex("target_user",
                Arrays.asList("@target_user", "TARGET_USER")) == -2, "ambiguous exact match");
        System.out.println("TikTokSearchSemanticsTest PASS");
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
