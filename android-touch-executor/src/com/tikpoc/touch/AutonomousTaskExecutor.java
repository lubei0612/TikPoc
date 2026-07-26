package com.tikpoc.touch;

import java.util.LinkedHashMap;
import java.util.Map;

public final class AutonomousTaskExecutor implements AutonomousTaskRunner.Executor {
    public enum Mode { SHADOW, ACTIVE }

    public interface Ui {
        void openProfile(Map<String, Object> target) throws Exception;
        Profile observeProfile() throws Exception;
        void openAndConfirmVideo(String videoKey) throws Exception;
        boolean applyAndConfirmAction(String action) throws Exception;
    }

    public static final class Profile {
        public final String username;
        public final boolean publicProfile;

        public Profile(String username, boolean publicProfile) {
            this.username = username;
            this.publicProfile = publicProfile;
        }
    }

    private final Ui ui;
    private final Mode mode;

    public AutonomousTaskExecutor(Ui ui, Mode mode) {
        if (ui == null || mode == null) throw new IllegalArgumentException("executor required");
        this.ui = ui;
        this.mode = mode;
    }

    @Override
    public DeviceTaskStore.Result execute(DeviceTaskStore.Task task) {
        String phase = "profile_opening";
        try {
            Map<String, Object> target = Protocol.decodeObject(task.payload);
            String expected = requiredString(target, "username");
            ui.openProfile(target);
            phase = "identity_confirmed";
            Profile profile = ui.observeProfile();
            if (!expected.equals(profile.username)) {
                return result(task, "deferred", phase, "target_identity_mismatch");
            }
            if (!profile.publicProfile) {
                return result(task, "deferred", phase, "profile_unavailable");
            }
            if (mode == Mode.SHADOW) return result(task, "completed", phase, "shadow_observed");
            if (!(target.get("video_key") instanceof String)
                    || !(target.get("action") instanceof String)) {
                return result(task, "deferred", phase, "missing_action_plan");
            }
            ui.openAndConfirmVideo((String) target.get("video_key"));
            if (!ui.applyAndConfirmAction((String) target.get("action"))) {
                return result(task, "uncertain", "action_reconciling", "action_unverified");
            }
            return result(task, "completed", "action_confirmed", "action_applied");
        } catch (Exception error) {
            return result(task, "deferred", phase, "executor_error");
        }
    }

    private static DeviceTaskStore.Result result(DeviceTaskStore.Task task,
            String state, String phase, String code) {
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("lease_id", task.leaseId);
        payload.put("state", state);
        payload.put("phase", phase);
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("code", code);
        payload.put("evidence", evidence);
        try {
            return new DeviceTaskStore.Result(
                    task.taskId + ":" + phase, task.taskId, Protocol.encodeObject(payload));
        } catch (Protocol.ProtocolException error) {
            throw new IllegalStateException("result encoding failed");
        }
    }

    private static String requiredString(Map<String, Object> values, String key)
            throws Protocol.ProtocolException {
        Object value = values.get(key);
        if (!(value instanceof String) || ((String) value).trim().isEmpty())
            throw new Protocol.ProtocolException("missing_" + key);
        return (String) value;
    }
}
