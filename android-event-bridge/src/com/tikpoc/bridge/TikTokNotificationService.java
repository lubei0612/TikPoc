package com.tikpoc.bridge;

import android.app.Notification;
import android.content.SharedPreferences;
import android.os.Build;
import android.provider.Settings;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.util.Log;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class TikTokNotificationService extends NotificationListenerService {
    private static final String TAG = "TikPocEventBridge";
    private static final String TIKTOK_PACKAGE = "com.zhiliaoapp.musically";
    private static final String PREFS = "bridge";
    private static final String PENDING_EVENTS = "pending_events";
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @Override
    public void onListenerConnected() {
        super.onListenerConnected();
        executor.submit(this::flushPending);
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (!TIKTOK_PACKAGE.equals(sbn.getPackageName())) return;
        Notification notification = sbn.getNotification();
        String title = stringExtra(notification, Notification.EXTRA_TITLE);
        String text = stringExtra(notification, Notification.EXTRA_TEXT);
        String category = notification.category == null ? "" : notification.category;
        String eventType = TikTokNotificationClassifier.classify(category, title, text);
        if (eventType == null) return;
        String defaultDeviceId = Build.MODEL + "-" + Settings.Secure.getString(
                getContentResolver(), Settings.Secure.ANDROID_ID);
        String deviceId = preferences()
                .getString("device_id", defaultDeviceId);
        String dedup = sha256(sbn.getKey() + ":" + sbn.getPostTime() + ":" + title + ":" + text);
        String body = "{"
                + "\"device_id\":\"" + escape(deviceId) + "\","
                + "\"event_type\":\"" + eventType + "\","
                + "\"dedup_key\":\"" + dedup + "\","
                + "\"payload\":{\"title\":\"" + escape(title)
                + "\",\"message\":\"" + escape(text)
                + "\",\"category\":\"" + escape(category) + "\"}}";
        executor.submit(() -> deliver(body));
    }

    private static String stringExtra(Notification notification, String key) {
        CharSequence value = notification.extras.getCharSequence(key);
        return value == null ? "" : value.toString();
    }

    private SharedPreferences preferences() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private String eventUrl() {
        return preferences().getString(
                "endpoint", "http://10.0.2.2:8766/api/device-events");
    }

    private void deliver(String body) {
        flushPending();
        if (!postWithRetry(eventUrl(), body)) remember(body);
    }

    private void flushPending() {
        Set<String> stored = preferences().getStringSet(
                PENDING_EVENTS, Collections.emptySet());
        Set<String> remaining = new HashSet<>(stored);
        if (remaining.isEmpty()) return;
        for (String body : new HashSet<>(remaining)) {
            if (postWithRetry(eventUrl(), body)) remaining.remove(body);
        }
        preferences().edit().putStringSet(PENDING_EVENTS, remaining).apply();
    }

    private void remember(String body) {
        Set<String> pending = new HashSet<>(preferences().getStringSet(
                PENDING_EVENTS, Collections.emptySet()));
        pending.add(body);
        preferences().edit().putStringSet(PENDING_EVENTS, pending).apply();
        Log.w(TAG, "Event delivery failed; queued for retry");
    }

    private static boolean postWithRetry(String eventUrl, String body) {
        for (int attempt = 0; attempt < 3; attempt++) {
            if (post(eventUrl, body)) return true;
            try {
                Thread.sleep(500L << attempt);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    private static boolean post(String eventUrl, String body) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(eventUrl).openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setDoOutput(true);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body.getBytes(StandardCharsets.UTF_8));
            }
            int status = connection.getResponseCode();
            return status >= 200 && status < 300;
        } catch (Exception error) {
            Log.w(TAG, "Event POST failed", error);
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }

    private static String sha256(String value) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder();
            for (byte b : bytes) result.append(String.format(Locale.ROOT, "%02x", b));
            return result.toString();
        } catch (Exception error) {
            return Integer.toHexString(value.hashCode());
        }
    }
}
