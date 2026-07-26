package com.tikpoc.touch;

import android.content.Context;
import android.content.SharedPreferences;

public final class AndroidProvisioningStore implements DeviceProvisioning.Store {
    private static final String PREFS = "tikpoc_device_settings";
    private static final String BASE_URL = "base_url";
    private static final String DEVICE_ID = "device_id";
    private static final String ACCOUNT_ID = "account_id";
    private static final String ROUND_ID = "round_id";
    private static final String SESSION_EPOCH = "session_epoch";
    private static final String WORKER_MODE = "worker_mode";
    private final SharedPreferences preferences;

    public AndroidProvisioningStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static String[] preferenceKeys() {
        return new String[] {
            BASE_URL, DEVICE_ID, ACCOUNT_ID, ROUND_ID, SESSION_EPOCH, WORKER_MODE
        };
    }

    @Override
    public void save(DeviceProvisioning.Settings settings) {
        if (!preferences.edit()
                .putString(BASE_URL, settings.baseUrl)
                .putString(DEVICE_ID, settings.deviceId)
                .putString(ACCOUNT_ID, settings.accountId)
                .putString(ROUND_ID, settings.roundId)
                .putLong(SESSION_EPOCH, settings.sessionEpoch)
                .putString(WORKER_MODE, settings.workerMode.name())
                .commit()) {
            throw new IllegalStateException("device settings persistence failed");
        }
    }

    @Override
    public DeviceProvisioning.Settings load() {
        String baseUrl = preferences.getString(BASE_URL, "");
        if (baseUrl == null || baseUrl.isEmpty()) return null;
        return new DeviceProvisioning.Settings(
                baseUrl,
                preferences.getString(DEVICE_ID, ""),
                preferences.getString(ACCOUNT_ID, ""),
                preferences.getString(ROUND_ID, ""),
                preferences.getLong(SESSION_EPOCH, 0L),
                DeviceProvisioning.WorkerMode.valueOf(
                        preferences.getString(WORKER_MODE, "SHADOW")),
                "");
    }
}
