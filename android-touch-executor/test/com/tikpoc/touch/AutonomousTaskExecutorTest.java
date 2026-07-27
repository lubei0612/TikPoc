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
        terminalSearchMissDoesNotBlockVerifiedContinuation();
        continuationReusesAlreadyVerifiedProfileSurface();
        continuationReacquiresProfileWhenCurrentSurfaceIsStale();
        activeModeVerifiesVideoAndAction();
        actionFailureUsesActionPhaseIdempotencyKey();
        reconciliationFailureRetainsActionPlan();
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

        check(result.idempotencyKey.equals("task-2:lease-2:action_executing"),
                "action failure does not collide with profile evidence receipt");
        check(result.payload.contains("\"state\":\"uncertain\""),
                "action evidence failure is reconciled once");
        check(result.payload.contains("\"phase\":\"action_executing\""),
                "action evidence failure terminates in execution phase");
        check(result.payload.contains("\"plan_id\":42"),
                "action evidence failure retains its immutable plan");
    }

    private static void reconciliationFailureRetainsActionPlan() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        ui.reconciliationError = new AccessibilityUiAdapter.UiException(
                "missing_control");
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task task = new DeviceTaskStore.Task(
                "task-reconcile", "lease-reconcile", 7L, 9_000L,
                "action_reconciling",
                "{\"username\":\"target_user\",\"plan_id\":43,"
                + "\"video_key\":\"video-1\",\"action\":\"repost\"}");

        DeviceTaskStore.Result result = executor.execute(task);

        check(result.idempotencyKey.equals(
                "task-reconcile:lease-reconcile:action_reconciling"),
                "reconciliation failure has reconciliation idempotency key");
        check(result.payload.contains("\"state\":\"deferred\""),
                "reconciliation failure is terminal after one read");
        check(result.payload.contains("\"plan_id\":43"),
                "reconciliation failure retains its immutable plan");
        check(ui.profiles == 0 && ui.videos == 0 && ui.actions == 0,
                "historical reconciliation only observes current action surface");
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

    private static void terminalSearchMissDoesNotBlockVerifiedContinuation() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        ui.openError = new AccessibilityUiAdapter.UiException(
                "profile_identity_mismatch");
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(
                ui, AutonomousTaskExecutor.Mode.ACTIVE);
        String payload = "{\"username\":\"target_user\",\"plan_id\":42,"
                + "\"video_key\":\"post:0\",\"action\":\"like\"}";

        DeviceTaskStore.Result initial = executor.execute(new DeviceTaskStore.Task(
                "task-initial", "lease-initial", 7L, 9_000L,
                "profile_opening", payload));
        DeviceTaskStore.Result continuation = executor.execute(new DeviceTaskStore.Task(
                "task-continuation", "lease-continuation", 7L, 9_000L,
                "video_opening", payload));

        check(initial.payload.contains("\"state\":\"skipped\""),
                "initial exact search miss is terminal");
        check(continuation.payload.contains("action_confirmed"),
                "verified continuation does not repeat failed navigation");
    }

    private static void continuationReusesAlreadyVerifiedProfileSurface() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(
                ui, AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task continuation = new DeviceTaskStore.Task(
                "task-continuation", "lease-continuation", 7L, 9_000L,
                "video_opening",
                "{\"username\":\"target_user\",\"plan_id\":42,"
                + "\"video_key\":\"post:0\",\"action\":\"like\"}");

        DeviceTaskStore.Result result = executor.execute(continuation);

        check(result.payload.contains("action_confirmed"), "continuation completes action");
        check(ui.profiles == 0, "continuation does not repeat verified profile navigation");
        check(ui.videos == 1 && ui.actions == 1, "continuation verifies planned action");
    }

    private static void continuationReacquiresProfileWhenCurrentSurfaceIsStale()
            throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        ui.observeFailuresRemaining = 1;
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(
                ui, AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task continuation = new DeviceTaskStore.Task(
                "task-stale", "lease-stale", 7L, 9_000L, "video_opening",
                "{\"username\":\"target_user\",\"plan_id\":42,"
                + "\"video_key\":\"post:0\",\"action\":\"like\"}");

        DeviceTaskStore.Result result = executor.execute(continuation);

        check(result.payload.contains("action_confirmed"), "stale continuation recovers");
        check(ui.profiles == 1, "stale continuation reacquires exact profile once");
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
        int profiles;
        RuntimeException observeError;
        RuntimeException openError;
        RuntimeException actionError;
        RuntimeException reconciliationError;
        RuntimeException videoError;
        int observeFailuresRemaining;

        FakeUi(String observedUsername, boolean publicProfile) {
            this.observedUsername = observedUsername;
            this.publicProfile = publicProfile;
        }

        @Override
        public void openProfile(Map<String, Object> target) {
            if (openError != null) throw openError;
            profiles++;
        }

        @Override
        public AutonomousTaskExecutor.Profile observeProfile() {
            if (observeFailuresRemaining > 0) {
                observeFailuresRemaining--;
                throw new AccessibilityUiAdapter.UiException("incomplete_profile_evidence");
            }
            if (observeError != null) throw observeError;
            return new AutonomousTaskExecutor.Profile(observedUsername, publicProfile);
        }

        @Override
        public void openAndConfirmVideo(String videoKey) {
            if (videoError != null) throw videoError;
            videos++;
        }

        @Override
        public boolean applyAndConfirmAction(String action) {
            if (actionError != null) throw actionError;
            actions++;
            return true;
        }

        @Override
        public boolean observeAction(String action) {
            if (reconciliationError != null) throw reconciliationError;
            return true;
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
