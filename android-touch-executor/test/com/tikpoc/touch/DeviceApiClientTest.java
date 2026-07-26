package com.tikpoc.touch;

import java.util.List;

public final class DeviceApiClientTest {
    public static void main(String[] args) throws Exception {
        registersAndReturnsScopedSession();
        requiresHttpsBaseUrl();
        pullsTypedTasksWithBearerAuthentication();
        preservesSearchNavigationModeInDurablePayload();
        postsHeartbeatAndIdempotentResult();
        redactsTokenFromTransportErrors();
        System.out.println("DeviceApiClientTest PASS");
    }

    private static void preservesSearchNavigationModeInDurablePayload() throws Exception {
        RecordingExchange exchange = new RecordingExchange();
        exchange.response = new DeviceApiClient.HttpResponse(200,
                "{\"tasks\":[{\"task_id\":\"20\",\"lease_id\":\"lease-20\","
                + "\"session_epoch\":7,\"lease_expires_at_ms\":9000,"
                + "\"phase\":\"pending\",\"navigation_mode\":\"search\","
                + "\"username\":\"target_user\"}]}");
        DeviceApiClient client = new DeviceApiClient(
                "https://api.example.test", "device-1", "secret", 7L, exchange);

        DeviceTaskStore.Task task = client.pull("round-1", 20).get(0);
        check(task.payload.contains("\"navigation_mode\":\"search\""),
                "search mode retained in durable payload");
        check(task.withPhase("profile_opening").payload.equals(task.payload),
                "search mode survives checkpoint");
    }

    private static void registersAndReturnsScopedSession() throws Exception {
        RecordingExchange exchange = new RecordingExchange();
        exchange.response = new DeviceApiClient.HttpResponse(200,
                "{\"device_id\":\"device-1\",\"account_id\":\"account-1\","
                + "\"session_epoch\":2,\"access_token\":\"scoped-token\"}");

        DeviceApiClient.Registration registration = DeviceApiClient.register(
                "https://api.example.test/root", "device-1", "account-1",
                "bootstrap-token", exchange);

        check(registration.deviceId.equals("device-1"), "registration device");
        check(registration.accountId.equals("account-1"), "registration account");
        check(registration.sessionEpoch == 2L, "registration epoch");
        check(registration.accessToken.equals("scoped-token"), "registration token");
        check(exchange.path.equals("/api/mobile/register"), "registration path");
        check(exchange.bearer.equals("bootstrap-token"), "registration bearer");
        check(exchange.baseUrl.equals("https://api.example.test/root"),
                "registration base URL");
    }

    private static void postsHeartbeatAndIdempotentResult() throws Exception {
        RecordingExchange exchange = new RecordingExchange();
        DeviceApiClient client = new DeviceApiClient(
                "https://api.example.test", "device-1", "secret", 7L, exchange);

        client.heartbeat("1.0.0", "idle", 3, 5_000L);
        check(exchange.path.equals("/api/mobile/heartbeat"), "heartbeat path");
        check(exchange.body.contains("\"queue_depth\":3"), "queue depth");

        exchange.response = new DeviceApiClient.HttpResponse(
                200, "{\"accepted\":false,\"state\":\"duplicate\"}");
        String state = client.uploadResult(new DeviceTaskStore.Result(
                "result-1", "19",
                "{\"lease_id\":\"lease-19\",\"state\":\"deferred\","
                + "\"phase\":\"profile_opening\",\"evidence\":{}}"));
        check(state.equals("duplicate"), "duplicate acknowledged");
        check(exchange.path.equals("/api/mobile/results"), "result path");
        check(exchange.body.contains("\"idempotency_key\":\"result-1\""),
                "idempotency key");
    }

    private static void requiresHttpsBaseUrl() {
        try {
            new DeviceApiClient("http://example.com", "device-1", "secret", 7L,
                    new RecordingExchange());
            throw new AssertionError("cleartext accepted");
        } catch (IllegalArgumentException expected) {
            check(expected.getMessage().equals("https base URL required"), "https error");
        }
    }

    private static void pullsTypedTasksWithBearerAuthentication() throws Exception {
        RecordingExchange exchange = new RecordingExchange();
        exchange.response = new DeviceApiClient.HttpResponse(200,
                "{\"tasks\":[{\"task_id\":\"19\",\"lease_id\":\"lease-19\","
                + "\"session_epoch\":7,\"lease_expires_at_ms\":9000,"
                + "\"phase\":\"pending\",\"username\":\"target_user\"}]}");
        DeviceApiClient client = new DeviceApiClient(
                "https://api.example.test", "device-1", "secret", 7L, exchange);

        List<DeviceTaskStore.Task> tasks = client.pull("round-1", 20);

        check(tasks.size() == 1, "one task");
        check(tasks.get(0).taskId.equals("19"), "task id");
        check(tasks.get(0).sessionEpoch == 7L, "session epoch");
        check(exchange.path.equals("/api/mobile/pull"), "pull path");
        check(exchange.bearer.equals("secret"), "bearer forwarded");
        check(exchange.body.contains("\"limit\":20"), "bounded limit");
        check(exchange.body.contains("\"round_id\":\"round-1\""), "round id");
    }

    private static void redactsTokenFromTransportErrors() throws Exception {
        RecordingExchange exchange = new RecordingExchange();
        exchange.failure = new java.io.IOException("network secret");
        DeviceApiClient client = new DeviceApiClient(
                "https://api.example.test", "device-1", "secret", 7L, exchange);
        try {
            client.pull("round-1", 20);
            throw new AssertionError("failure accepted");
        } catch (DeviceApiClient.ApiException expected) {
            check(expected.getMessage().equals("mobile_api_transport"), "bounded error");
            check(!expected.getMessage().contains("secret"), "token redacted");
        }
    }

    private static final class RecordingExchange implements DeviceApiClient.Exchange {
        String baseUrl = "";
        String path = "";
        String bearer = "";
        String body = "";
        DeviceApiClient.HttpResponse response = new DeviceApiClient.HttpResponse(200, "{}");
        java.io.IOException failure;

        @Override
        public DeviceApiClient.HttpResponse post(
                String baseUrl, String path, String bearer, String body) throws Exception {
            this.baseUrl = baseUrl;
            this.path = path;
            this.bearer = bearer;
            this.body = body;
            if (failure != null) throw failure;
            return response;
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
