package com.tikpoc.bridge;

public final class TikTokNotificationClassifierTest {
    public static void main(String[] args) {
        assertType("new_follower", null, "Alex", "Alex followed you");
        assertType("new_follower", null, "TikTok", "小陈关注了你");
        assertType("new_follower", null, "TikTok", "小陳關注了你");
        assertType("dm_received", "msg", "Alex", "hello");
        assertType("dm_received", null, "TikTok", "Alex sent you a message");
        assertType(null, null, "TikTok", "Your video received 10 likes");
    }

    private static void assertType(
            String expected, String category, String title, String text) {
        String actual = TikTokNotificationClassifier.classify(category, title, text);
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + " actual=" + actual);
        }
    }
}
