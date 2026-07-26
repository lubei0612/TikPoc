package com.tikpoc.touch;

import java.util.LinkedHashMap;
import java.util.ArrayList;
import java.util.List;
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
        public final long following;
        public final long followers;
        public final long videoCount;
        public final List<String> postHandles;

        public Profile(String username, boolean publicProfile) {
            this(username, publicProfile, 0L, 0L, 0L);
        }

        public Profile(String username, boolean publicProfile, long following,
                long followers, long videoCount) {
            this(username, publicProfile, following, followers, videoCount,
                    new ArrayList<String>());
        }

        public Profile(String username, boolean publicProfile, long following,
                long followers, long videoCount, List<String> postHandles) {
            this.username = username;
            this.publicProfile = publicProfile;
            this.following = following;
            this.followers = followers;
            this.videoCount = videoCount;
            this.postHandles = postHandles;
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
            if (mode == Mode.SHADOW) return profileResult(task, profile, phase, "shadow_observed");
            if (!(target.get("video_key") instanceof String)
                    || !(target.get("action") instanceof String)) {
                return result(task, "deferred", phase, "missing_action_plan");
            }
            ui.openAndConfirmVideo((String) target.get("video_key"));
            if (!ui.applyAndConfirmAction((String) target.get("action"))) {
                return result(task, "uncertain", "action_reconciling", "action_unverified");
            }
            return result(task, "completed", "action_executing", "action_confirmed");
        } catch (Exception error) {
            return result(task, "deferred", phase, "executor_error");
        }
    }

    private static DeviceTaskStore.Result profileResult(DeviceTaskStore.Task task,
            Profile profile, String phase, String code) {
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("lease_id", task.leaseId);
        payload.put("state", "completed");
        payload.put("phase", phase);
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("code", code);
        try {
            Object planId = Protocol.decodeObject(task.payload).get("plan_id");
            if (planId instanceof Long) evidence.put("plan_id", planId);
        } catch (Protocol.ProtocolException ignored) {}
        evidence.put("observed_username", profile.username);
        evidence.put("access_state", profile.publicProfile ? "available" : "unavailable");
        evidence.put("following", profile.following);
        evidence.put("followers", profile.followers);
        evidence.put("video_count", profile.videoCount);
        evidence.put("post_handles", profile.postHandles);
        payload.put("evidence", evidence);
        try {
            return new DeviceTaskStore.Result(
                    task.taskId + ":" + phase, task.taskId, Protocol.encodeObject(payload));
        } catch (Protocol.ProtocolException error) {
            throw new IllegalStateException("result encoding failed");
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
