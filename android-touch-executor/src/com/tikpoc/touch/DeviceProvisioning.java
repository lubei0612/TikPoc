package com.tikpoc.touch;

public final class DeviceProvisioning {
    public interface Store {
        void save(Settings settings) throws Exception;
        Settings load() throws Exception;
    }

    public interface Vault {
        void save(String accessToken) throws Exception;
        String load() throws Exception;
    }

    public static final class Settings {
        public final String baseUrl;
        public final String deviceId;
        public final String accountId;
        public final String roundId;
        public final long sessionEpoch;
        public final String accessToken;

        public Settings(String baseUrl, String deviceId, String accountId,
                String roundId, long sessionEpoch, String accessToken) {
            this.baseUrl = baseUrl;
            this.deviceId = deviceId;
            this.accountId = accountId;
            this.roundId = roundId;
            this.sessionEpoch = sessionEpoch;
            this.accessToken = accessToken;
        }
    }

    private DeviceProvisioning() {}

    public static void save(Store store, Vault vault, String baseUrl,
            String deviceId, String accountId, String roundId,
            long sessionEpoch, String accessToken) throws Exception {
        validate(baseUrl, deviceId, accountId, roundId, sessionEpoch, accessToken);
        vault.save(accessToken);
        store.save(new Settings(baseUrl, deviceId, accountId, roundId, sessionEpoch, ""));
    }

    public static Settings load(Store store, Vault vault) throws Exception {
        Settings settings = store.load();
        if (settings == null) return null;
        String token = vault.load();
        validate(settings.baseUrl, settings.deviceId, settings.accountId,
                settings.roundId, settings.sessionEpoch, token);
        return new Settings(settings.baseUrl, settings.deviceId, settings.accountId,
                settings.roundId, settings.sessionEpoch, token);
    }

    private static void validate(String baseUrl, String deviceId, String accountId,
            String roundId, long sessionEpoch, String accessToken) {
        if (baseUrl == null || !baseUrl.startsWith("https://"))
            throw new IllegalArgumentException("https base URL required");
        if (empty(deviceId) || empty(accountId) || empty(roundId)
                || sessionEpoch <= 0 || empty(accessToken))
            throw new IllegalArgumentException("invalid device provisioning");
    }

    private static boolean empty(String value) {
        return value == null || value.trim().isEmpty();
    }
}
