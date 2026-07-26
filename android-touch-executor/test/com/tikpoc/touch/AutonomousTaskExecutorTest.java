package com.tikpoc.touch;

import java.util.Map;

public final class AutonomousTaskExecutorTest {
    public static void main(String[] args) throws Exception {
        shadowModeConfirmsIdentityWithoutAction();
        identityMismatchBecomesDeferredResult();
        activeModePublishesProfileBeforeActionPlanExists();
        activeModeTreatsEmptyEnvelopePlanFieldsAsUnplanned();
        activeTraceOpensVideoWithoutApplyingAnInteraction();
        preservesDeviceEvidenceErrorCode();
        activeModeVerifiesVideoAndAction();
        actionFailureUsesActionPhaseIdempotencyKey();
        System.out.println("AutonomousTaskExecutorTest PASS");
    }

    private static void activeModeVerifiesVideoAndAction() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task planned = new DeviceTaskStore.Task(
                "task-2", "lease-2", 7L, 9_000L, "pending",
                "{\"username\":\"target_user\",\"plan_id\":42,"
                + "\"video_key\":\"video-1\","
                + "\"action\":\"like\"}");

        DeviceTaskStore.Result result = executor.execute(planned);

        check(result.payload.contains("action_confirmed"), "action confirmed");
        check(result.payload.contains("\"plan_id\":42"), "action plan identified");
        check(ui.videos == 1, "video verified");
        check(ui.actions == 1, "one action");
    }

    private static void actionFailureUsesActionPhaseIdempotencyKey() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        ui.actionError = new AccessibilityUiAdapter.UiException(
                "device_evidence_unavailable");
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task planned = new DeviceTaskStore.Task(
                "task-2", "lease-2", 7L, 9_000L, "pending",
                "{\"username\":\"target_user\",\"plan_id\":42,"
                + "\"video_key\":\"video-1\",\"action\":\"favorite\"}");

        DeviceTaskStore.Result result = executor.execute(planned);

        check(result.idempotencyKey.equals("task-2:action_executing"),
                "action failure does not collide with profile evidence receipt");
        check(result.payload.contains("\"state\":\"uncertain\""),
                "action evidence failure is reconciled once");
        check(result.payload.contains("\"phase\":\"action_reconciling\""),
                "action evidence failure enters reconciliation");
        check(result.payload.contains("\"plan_id\":42"),
                "action evidence failure retains its immutable plan");
    }

    private static void shadowModeConfirmsIdentityWithoutAction() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.SHADOW);
        DeviceTaskStore.Result result = executor.execute(task("target_user", "{}"));

        check(result.payload.contains("identity_confirmed"), "identity confirmed");
        check(ui.actions == 0, "shadow has no action");
    }

    private static void identityMismatchBecomesDeferredResult() throws Exception {
        FakeUi ui = new FakeUi("other_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.SHADOW);
        DeviceTaskStore.Result result = executor.execute(task("target_user", "{}"));

        check(result.payload.contains("target_identity_mismatch"), "mismatch deferred");
        check(ui.actions == 0, "mismatch has no action");
    }

    private static void activeModePublishesProfileBeforeActionPlanExists() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Result result = executor.execute(task("target_user", "{}"));

        check(result.payload.contains("profile_observed"), "profile evidence published");
        check(result.payload.contains("\"video_count\":0"), "profile metrics included");
        check(ui.actions == 0, "no action without plan");
    }

    private static void activeModeTreatsEmptyEnvelopePlanFieldsAsUnplanned()
            throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task task = new DeviceTaskStore.Task(
                "task-empty", "lease-empty", 7L, 9_000L, "pending",
                "{\"username\":\"target_user\",\"video_key\":\"\",\"action\":\"\"}");

        DeviceTaskStore.Result result = executor.execute(task);

        check(result.payload.contains("profile_observed"), "empty plan awaits server plan");
        check(ui.videos == 0 && ui.actions == 0, "empty plan performs no action");
    }

    private static void activeTraceOpensVideoWithoutApplyingAnInteraction()
            throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task trace = new DeviceTaskStore.Task(
                "task-trace", "lease-trace", 7L, 9_000L, "pending",
                "{\"username\":\"target_user\",\"plan_id\":43,"
                + "\"video_key\":\"post:0\","
                + "\"action\":\"trace\"}");

        DeviceTaskStore.Result result = executor.execute(trace);

        check(result.payload.contains("trace_confirmed"), "trace confirmed");
        check(result.payload.contains("\"plan_id\":43"), "trace plan identified");
        check(ui.videos == 1, "trace opens one video");
        check(ui.actions == 0, "trace applies no interaction");
    }

    private static void preservesDeviceEvidenceErrorCode() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        ui.observeError = new AccessibilityUiAdapter.UiException("stale_tree");
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);

        DeviceTaskStore.Result result = executor.execute(task("target_user", "{}"));

        check(result.payload.contains("stale_tree"), "semantic failure preserved");
    }

    private static DeviceTaskStore.Task task(String username, String payload) {
        return new DeviceTaskStore.Task("task-1", "lease-1", 7L, 9_000L,
                "pending", "{\"username\":\"" + username + "\"}");
    }

    private static final class FakeUi implements AutonomousTaskExecutor.Ui {
        final String observedUsername;
        final boolean publicProfile;
        int actions;
        int videos;
        RuntimeException observeError;
        RuntimeException actionError;

        FakeUi(String observedUsername, boolean publicProfile) {
            this.observedUsername = observedUsername;
            this.publicProfile = publicProfile;
        }

        @Override
        public void openProfile(Map<String, Object> target) {}

        @Override
        public AutonomousTaskExecutor.Profile observeProfile() {
            if (observeError != null) throw observeError;
            return new AutonomousTaskExecutor.Profile(observedUsername, publicProfile);
        }

        @Override
        public void openAndConfirmVideo(String videoKey) { videos++; }

        @Override
        public boolean applyAndConfirmAction(String action) {
            if (actionError != null) throw actionError;
            actions++;
            return true;
        }

        @Override
        public boolean observeAction(String action) { return true; }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
