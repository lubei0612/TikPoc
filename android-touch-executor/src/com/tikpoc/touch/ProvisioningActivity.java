package com.tikpoc.touch;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

/** One-shot operator bootstrap entry point. It accepts no implicit intents. */
public final class ProvisioningActivity extends Activity {
    public static final String BASE_URL = "base_url";
    public static final String DEVICE_ID = "device_id";
    public static final String ACCOUNT_ID = "account_id";
    public static final String ROUND_ID = "round_id";
    public static final String BOOTSTRAP_TOKEN = "bootstrap_token";
    public static final String WORKER_MODE = "worker_mode";

    private TextView status;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        status = new TextView(this);
        status.setText("TikPoc provisioning…");
        status.setPadding(32, 48, 32, 48);
        setContentView(status);

        final String baseUrl = getIntent().getStringExtra(BASE_URL);
        final String deviceId = getIntent().getStringExtra(DEVICE_ID);
        final String accountId = getIntent().getStringExtra(ACCOUNT_ID);
        final String roundId = getIntent().getStringExtra(ROUND_ID);
        final String bootstrapToken = getIntent().getStringExtra(BOOTSTRAP_TOKEN);
        final String workerMode = getIntent().getStringExtra(WORKER_MODE);
        // Remove the one-time values from the Activity intent as soon as read.
        setIntent(new android.content.Intent());

        new Thread(() -> provision(baseUrl, deviceId, accountId, roundId,
                        bootstrapToken, workerMode),
                "tikpoc-provisioning").start();
    }

    private void provision(String baseUrl, String deviceId, String accountId,
            String roundId, String bootstrapToken, String workerMode) {
        try {
            DeviceApiClient.Registration registration = DeviceApiClient.register(
                    baseUrl, deviceId, accountId, bootstrapToken,
                    new DeviceApiClient.HttpsExchange());
            new DeviceTaskStore(new AndroidTaskBackend(this)).clear();
            DeviceProvisioning.save(new AndroidProvisioningStore(this),
                    new AndroidTokenVault(this), baseUrl, registration.deviceId,
                    registration.accountId, roundId, registration.sessionEpoch,
                    parseWorkerMode(workerMode), registration.accessToken);
            show("Provisioned " + registration.deviceId + "\nSession ready.\n"
                    + "Enable TikPoc Accessibility service to start the worker.");
        } catch (Exception error) {
            show("Provisioning failed: " + error.getMessage());
        }
    }

    private static DeviceProvisioning.WorkerMode parseWorkerMode(String value) {
        if (value == null || value.trim().isEmpty()) {
            return DeviceProvisioning.WorkerMode.SHADOW;
        }
        return DeviceProvisioning.WorkerMode.valueOf(value.trim().toUpperCase());
    }

    private void show(final String value) {
        runOnUiThread(() -> status.setText(value));
    }
}
