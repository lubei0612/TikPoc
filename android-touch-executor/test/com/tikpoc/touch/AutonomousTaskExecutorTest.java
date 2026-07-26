package com.tikpoc.touch;

import java.util.Map;

public final class AutonomousTaskExecutorTest {
    public static void main(String[] args) throws Exception {
        shadowModeConfirmsIdentityWithoutAction();
        identityMismatchBecomesDeferredResult();
        activeModeRequiresImmutableActionPlan();
        activeModeVerifiesVideoAndAction();
        System.out.println("AutonomousTaskExecutorTest PASS");
    }

    private static void activeModeVerifiesVideoAndAction() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Task planned = new DeviceTaskStore.Task(
                "task-2", "lease-2", 7L, 9_000L, "pending",
                "{\"username\":\"target_user\",\"video_key\":\"video-1\","
                + "\"action\":\"like\"}");

        DeviceTaskStore.Result result = executor.execute(planned);

        check(result.payload.contains("action_confirmed"), "action confirmed");
        check(ui.videos == 1, "video verified");
        check(ui.actions == 1, "one action");
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

    private static void activeModeRequiresImmutableActionPlan() throws Exception {
        FakeUi ui = new FakeUi("target_user", true);
        AutonomousTaskExecutor executor = new AutonomousTaskExecutor(ui,
                AutonomousTaskExecutor.Mode.ACTIVE);
        DeviceTaskStore.Result result = executor.execute(task("target_user", "{}"));

        check(result.payload.contains("missing_action_plan"), "plan required");
        check(ui.actions == 0, "no action without plan");
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

        FakeUi(String observedUsername, boolean publicProfile) {
            this.observedUsername = observedUsername;
            this.publicProfile = publicProfile;
        }

        @Override
        public void openProfile(Map<String, Object> target) {}

        @Override
        public AutonomousTaskExecutor.Profile observeProfile() {
            return new AutonomousTaskExecutor.Profile(observedUsername, publicProfile);
        }

        @Override
        public void openAndConfirmVideo(String videoKey) { videos++; }

        @Override
        public boolean applyAndConfirmAction(String action) {
            actions++;
            return true;
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
