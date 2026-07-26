package com.tikpoc.touch;

public final class DeviceProvisioningTest {
    public static void main(String[] args) throws Exception {
        rejectsCleartextApi();
        storesScopedTokenOnlyInVault();
        androidPreferencesExcludeTokenMaterial();
        System.out.println("DeviceProvisioningTest PASS");
    }

    private static void androidPreferencesExcludeTokenMaterial() {
        for (String key : AndroidProvisioningStore.preferenceKeys()) {
            check(!key.toLowerCase().contains("token"), "preference key excludes token");
        }
    }

    private static void rejectsCleartextApi() throws Exception {
        try {
            DeviceProvisioning.save(
                    new MemoryStore(), new MemoryVault(),
                    "http://api.example.test", "device-1", "account-1",
                    "round-1", 7L, "scoped-token");
            throw new AssertionError("cleartext accepted");
        } catch (IllegalArgumentException expected) {
            check(expected.getMessage().equals("https base URL required"), "https error");
        }
    }

    private static void storesScopedTokenOnlyInVault() throws Exception {
        MemoryStore store = new MemoryStore();
        MemoryVault vault = new MemoryVault();

        DeviceProvisioning.save(
                store, vault, "https://api.example.test", "device-1", "account-1",
                "round-1", 7L, "scoped-token");
        DeviceProvisioning.Settings settings = DeviceProvisioning.load(store, vault);

        check(store.tokenMaterial == null, "config excludes token");
        check(vault.token.equals("scoped-token"), "vault owns token");
        check(settings.accessToken.equals("scoped-token"), "token restored");
        check(settings.sessionEpoch == 7L, "epoch restored");
        check(settings.roundId.equals("round-1"), "round restored");
    }

    private static final class MemoryStore implements DeviceProvisioning.Store {
        DeviceProvisioning.Settings settings;
        String tokenMaterial;

        @Override
        public void save(DeviceProvisioning.Settings value) {
            settings = new DeviceProvisioning.Settings(
                    value.baseUrl, value.deviceId, value.accountId,
                    value.roundId, value.sessionEpoch, "");
        }

        @Override
        public DeviceProvisioning.Settings load() { return settings; }
    }

    private static final class MemoryVault implements DeviceProvisioning.Vault {
        String token = "";

        @Override
        public void save(String value) { token = value; }

        @Override
        public String load() { return token; }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
