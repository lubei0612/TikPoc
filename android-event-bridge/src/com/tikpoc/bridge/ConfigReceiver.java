package com.tikpoc.bridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class ConfigReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String endpoint = intent.getStringExtra("endpoint");
        String deviceId = intent.getStringExtra("device_id");
        if (endpoint == null || deviceId == null) return;
        context.getSharedPreferences("bridge", Context.MODE_PRIVATE)
                .edit()
                .putString("endpoint", endpoint)
                .putString("device_id", deviceId)
                .apply();
    }
}
