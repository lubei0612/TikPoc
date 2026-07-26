package com.tikpoc.touch;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.net.URLConnection;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.net.ssl.HttpsURLConnection;

public final class DeviceApiClient implements AutonomousTaskRunner.Client {
    public interface Exchange {
        HttpResponse post(String baseUrl, String path, String bearer, String body)
                throws Exception;
    }

    public static final class HttpResponse {
        public final int status;
        public final String body;

        public HttpResponse(int status, String body) {
            this.status = status;
            this.body = body == null ? "" : body;
        }
    }

    public static final class ApiException extends Exception {
        public ApiException(String code) { super(code); }
    }

    public static final class Registration {
        public final String deviceId;
        public final String accountId;
        public final long sessionEpoch;
        public final String accessToken;

        private Registration(String deviceId, String accountId,
                long sessionEpoch, String accessToken) {
            this.deviceId = deviceId;
            this.accountId = accountId;
            this.sessionEpoch = sessionEpoch;
            this.accessToken = accessToken;
        }
    }

    public static Registration register(String baseUrl, String deviceId,
            String accountId, String bootstrapToken, Exchange exchange) throws ApiException {
        if (baseUrl == null || !baseUrl.startsWith("https://"))
            throw new IllegalArgumentException("https base URL required");
        if (deviceId == null || deviceId.trim().isEmpty()
                || accountId == null || accountId.trim().isEmpty()
                || bootstrapToken == null || bootstrapToken.trim().isEmpty()
                || exchange == null) {
            throw new IllegalArgumentException("invalid mobile registration");
        }
        Map<String, Object> request = new LinkedHashMap<String, Object>();
        request.put("device_id", deviceId);
        request.put("account_id", accountId);
        final String body;
        try {
            body = Protocol.encodeObject(request);
        } catch (Protocol.ProtocolException error) {
            throw new ApiException("mobile_api_payload");
        }
        final HttpResponse response;
        try {
            response = exchange.post(trimTrailingSlash(baseUrl), "/api/mobile/register",
                    bootstrapToken, body);
        } catch (Exception error) {
            throw new ApiException("mobile_api_transport");
        }
        if (response.status < 200 || response.status >= 300)
            throw new ApiException(response.status == 409
                    ? "mobile_registration_conflict" : "mobile_registration_rejected");
        try {
            Map<String, Object> values = Protocol.decodeObject(response.body);
            return new Registration(string(values, "device_id"),
                    string(values, "account_id"), positive(values, "session_epoch"),
                    string(values, "access_token"));
        } catch (Protocol.ProtocolException error) {
            throw new ApiException("mobile_api_invalid_response");
        }
    }

    private final String baseUrl;
    private final String deviceId;
    private final String bearer;
    private final long sessionEpoch;
    private final Exchange exchange;

    public DeviceApiClient(String baseUrl, String deviceId, String bearer,
            long sessionEpoch, Exchange exchange) {
        if (baseUrl == null || !baseUrl.startsWith("https://"))
            throw new IllegalArgumentException("https base URL required");
        if (deviceId == null || deviceId.trim().isEmpty() || bearer == null
                || bearer.trim().isEmpty() || sessionEpoch <= 0 || exchange == null)
            throw new IllegalArgumentException("invalid mobile API configuration");
        this.baseUrl = trimTrailingSlash(baseUrl);
        this.deviceId = deviceId;
        this.bearer = bearer;
        this.sessionEpoch = sessionEpoch;
        this.exchange = exchange;
    }

    public List<DeviceTaskStore.Task> pull(String roundId, int limit) throws ApiException {
        if (roundId == null || roundId.trim().isEmpty() || limit < 1 || limit > 50)
            throw new IllegalArgumentException("invalid mobile pull");
        Map<String, Object> request = new LinkedHashMap<String, Object>();
        request.put("device_id", deviceId);
        request.put("session_epoch", sessionEpoch);
        request.put("round_id", roundId);
        request.put("limit", (long) limit);
        Map<String, Object> response = post("/api/mobile/pull", request);
        Object raw = response.get("tasks");
        if (!(raw instanceof List)) throw new ApiException("mobile_api_invalid_response");
        List<DeviceTaskStore.Task> tasks = new ArrayList<DeviceTaskStore.Task>();
        for (Object value : (List<?>) raw) {
            if (!(value instanceof Map)) throw new ApiException("mobile_api_invalid_response");
            @SuppressWarnings("unchecked") Map<String, Object> task = (Map<String, Object>) value;
            tasks.add(new DeviceTaskStore.Task(
                    string(task, "task_id"), string(task, "lease_id"),
                    positive(task, "session_epoch"), positive(task, "lease_expires_at_ms"),
                    string(task, "phase"), encodeTask(task)));
        }
        return tasks;
    }

    public void heartbeat(String appVersion, String phase, int queueDepth,
            long clientTimestampMs) throws ApiException {
        if (appVersion == null || appVersion.trim().isEmpty() || phase == null
                || phase.trim().isEmpty() || queueDepth < 0 || queueDepth > 50
                || clientTimestampMs < 0) {
            throw new IllegalArgumentException("invalid mobile heartbeat");
        }
        Map<String, Object> request = new LinkedHashMap<String, Object>();
        request.put("device_id", deviceId);
        request.put("session_epoch", sessionEpoch);
        request.put("app_version", appVersion);
        request.put("phase", phase);
        request.put("queue_depth", (long) queueDepth);
        request.put("client_timestamp_ms", clientTimestampMs);
        post("/api/mobile/heartbeat", request);
    }

    public String uploadResult(DeviceTaskStore.Result result) throws ApiException {
        if (result == null) throw new IllegalArgumentException("result required");
        final Map<String, Object> request;
        try {
            request = Protocol.decodeObject(result.payload);
        } catch (Protocol.ProtocolException error) {
            throw new ApiException("mobile_api_result_payload");
        }
        request.put("device_id", deviceId);
        request.put("session_epoch", sessionEpoch);
        request.put("task_id", result.taskId);
        request.put("idempotency_key", result.idempotencyKey);
        Map<String, Object> response = post("/api/mobile/results", request);
        Object state = response.get("state");
        if (!(state instanceof String)) throw new ApiException("mobile_api_invalid_response");
        return (String) state;
    }

    private Map<String, Object> post(String path, Map<String, Object> request)
            throws ApiException {
        final String body;
        try {
            body = Protocol.encodeObject(request);
        } catch (Protocol.ProtocolException error) {
            throw new ApiException("mobile_api_payload");
        }
        final HttpResponse response;
        try {
            response = exchange.post(baseUrl, path, bearer, body);
        } catch (Exception error) {
            throw new ApiException("mobile_api_transport");
        }
        if (response.status < 200 || response.status >= 300) {
            if (response.status == 409) throw new ApiException("stale_session");
            throw new ApiException("mobile_api_rejected");
        }
        try {
            return Protocol.decodeObject(response.body);
        } catch (Protocol.ProtocolException error) {
            throw new ApiException("mobile_api_invalid_response");
        }
    }

    public static final class HttpsExchange implements Exchange {
        @Override
        public HttpResponse post(String baseUrl, String path, String bearer, String body)
                throws Exception {
            URL url = new URL(baseUrl + path);
            URLConnection opened = url.openConnection();
            if (!(opened instanceof HttpsURLConnection))
                throw new IOException("https required");
            HttpsURLConnection connection = (HttpsURLConnection) opened;
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(15_000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
            connection.setRequestProperty("Content-Type", "application/json");
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body.getBytes("UTF-8"));
            }
            int status = connection.getResponseCode();
            InputStream input = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            return new HttpResponse(status, readBounded(input));
        }
    }

    private static String trimTrailingSlash(String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private static String string(Map<String, Object> values, String key) throws ApiException {
        Object value = values.get(key);
        if (!(value instanceof String) || ((String) value).trim().isEmpty())
            throw new ApiException("mobile_api_invalid_response");
        return (String) value;
    }

    private static long positive(Map<String, Object> values, String key) throws ApiException {
        Object value = values.get(key);
        if (!(value instanceof Long) || (Long) value <= 0) throw new ApiException("mobile_api_invalid_response");
        return (Long) value;
    }

    private static String encodeTask(Map<String, Object> task) throws ApiException {
        try {
            return Protocol.encodeObject(task);
        } catch (Protocol.ProtocolException error) {
            throw new ApiException("mobile_api_invalid_response");
        }
    }

    private static String readBounded(InputStream input) throws IOException {
        if (input == null) return "";
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int total = 0;
        int count;
        while ((count = input.read(buffer)) != -1) {
            total += count;
            if (total > Protocol.MAX_REQUEST_BYTES) throw new IOException("response too large");
            output.write(buffer, 0, count);
        }
        return output.toString("UTF-8");
    }
}
