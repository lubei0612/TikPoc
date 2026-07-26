package com.tikpoc.touch;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

public class AccessibilityUiAdapter implements AutonomousTaskExecutor.Ui {
    public static final class UiException extends RuntimeException {
        public final String code;

        public UiException(String code) {
            super(code);
            this.code = code;
        }
    }

    public interface ElapsedClock {
        long nowMs();
    }

    public interface Invoker {
        Protocol.Response invoke(Protocol.Request request) throws Exception;
    }

    private final String deviceId;
    private final String accountId;
    private final long fenceToken;
    private final ElapsedClock clock;
    private final Invoker invoker;
    private Map<String, Object> target = new LinkedHashMap<String, Object>();

    public AccessibilityUiAdapter(String deviceId, String accountId, long fenceToken,
            long nowMs, Invoker invoker) {
        this(deviceId, accountId, fenceToken, () -> nowMs, invoker);
    }

    public AccessibilityUiAdapter(String deviceId, String accountId, long fenceToken,
            ElapsedClock clock, Invoker invoker) {
        this.deviceId = deviceId;
        this.accountId = accountId;
        this.fenceToken = fenceToken;
        if (clock == null) throw new IllegalArgumentException("clock required");
        this.clock = clock;
        this.invoker = invoker;
    }

    @Override
    public void openProfile(Map<String, Object> target) throws Exception {
        this.target = new LinkedHashMap<String, Object>(target);
        String targetId = string(target, "target_id");
        String route = targetId.isEmpty()
                ? string(target, "profile_url")
                : "snssdk1233://user/profile/" + targetId;
        Map<String, Object> arguments = map(
                "route", route, "expected_username", string(target, "username"));
        try {
            request("open_profile", "profile_opening", arguments);
        } catch (UiException error) {
            String fallback = string(target, "profile_url");
            if (targetId.isEmpty() || fallback.isEmpty()
                    || !("profile_identity_mismatch".equals(error.code)
                    || "profile_not_updated".equals(error.code)
                    || "profile_evidence_unavailable".equals(error.code))) throw error;
            request("open_profile", "profile_opening", map(
                    "route", fallback,
                    "expected_username", string(target, "username")));
        }
    }

    @Override
    public AutonomousTaskExecutor.Profile observeProfile() throws Exception {
        Protocol.Response response = request(
                "observe_profile", "identity_confirmed",
                map("expected_username", string(target, "username")));
        Map<String, Object> evidence = evidence(response);
        List<String> handles = new ArrayList<String>();
        Object rawHandles = evidence.get("post_handles");
        if (rawHandles instanceof List) {
            for (Object handle : (List<?>) rawHandles) {
                if (handle instanceof String && !((String) handle).isEmpty()) {
                    handles.add((String) handle);
                }
            }
        }
        return new AutonomousTaskExecutor.Profile(
                string(evidence, "username"),
                "available".equals(string(evidence, "access_state")),
                number(evidence, "following"), number(evidence, "followers"),
                number(evidence, "video_count"), handles);
    }

    @Override
    public void openAndConfirmVideo(String videoKey) throws Exception {
        request("open_video", "video_opening", map("video_key", videoKey));
    }

    @Override
    public boolean applyAndConfirmAction(String action) throws Exception {
        Protocol.Response response = request(
                "apply_action", "action_executing", map("action", action), true);
        Map<String, Object> evidence = evidence(response);
        return "on".equals(string(evidence, "after"));
    }

    @Override
    public boolean observeAction(String action) throws Exception {
        Protocol.Response response = request(
                "observe_action", "action_reconciling", map("action", action));
        return "on".equals(string(evidence(response), "state"));
    }

    protected Protocol.Response invoke(Protocol.Request request) throws Exception {
        return invoker.invoke(request);
    }

    private Protocol.Response request(String command, String phase,
            Map<String, Object> arguments) throws Exception {
        return request(command, phase, arguments, false);
    }

    private Protocol.Response request(String command, String phase,
            Map<String, Object> arguments, boolean allowUncertain) throws Exception {
        Map<String, Object> values = new LinkedHashMap<String, Object>();
        values.put("version", 1L);
        values.put("command_id", UUID.randomUUID().toString());
        values.put("command", command);
        values.put("device_id", deviceId);
        values.put("account_id", accountId);
        values.put("fence_token", fenceToken);
        values.put("assignment_id", assignmentId());
        values.put("phase", phase);
        long nowMs = clock.nowMs();
        values.put("deadline_elapsed_ms", nowMs + 10_000L);
        values.put("arguments", arguments);
        Protocol.Request request = Protocol.parseRequest(Protocol.encodeObject(values), nowMs);
        Protocol.Response response = invoke(request);
        Object status = response.values.get("status");
        if (allowUncertain && "uncertain".equals(status)) return response;
        if (!"ok".equals(status)) {
            Object rawError = response.values.get("error");
            if (rawError instanceof Map) {
                Object code = ((Map<?, ?>) rawError).get("code");
                if (code instanceof String && !((String) code).isEmpty()) {
                    throw new UiException((String) code);
                }
            }
            throw new UiException("device_evidence_unavailable");
        }
        return response;
    }

    private long assignmentId() {
        try { return Long.parseLong(string(target, "task_id")); }
        catch (NumberFormatException error) { return 1L; }
    }

    private static Map<String, Object> evidence(Protocol.Response response) throws Exception {
        Object raw = response.values.get("evidence");
        if (!(raw instanceof Map)) throw new IllegalStateException("evidence missing");
        @SuppressWarnings("unchecked") Map<String, Object> values = (Map<String, Object>) raw;
        return values;
    }

    private static Map<String, Object> map(String firstKey, String firstValue,
            String secondKey, String secondValue) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put(firstKey, firstValue);
        result.put(secondKey, secondValue);
        return result;
    }

    private static Map<String, Object> map(String key, String value) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put(key, value);
        return result;
    }

    private static String string(Map<String, Object> values, String key) {
        Object value = values.get(key);
        return value instanceof String ? (String) value : "";
    }

    private static long number(Map<String, Object> values, String key) {
        Object value = values.get(key);
        return value instanceof Number ? ((Number) value).longValue() : 0L;
    }
}
