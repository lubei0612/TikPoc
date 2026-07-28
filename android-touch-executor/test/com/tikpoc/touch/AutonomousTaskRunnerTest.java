package com.tikpoc.touch;

import java.util.ArrayList;
import java.util.List;

public final class AutonomousTaskRunnerTest {
    public static void main(String[] args) throws Exception {
        pullsTasksAndFlushesIdempotentOutbox();
        executesOnePersistedTaskAndQueuesResult();
        appliesPacingAfterACompletedTarget();
        activeQueueUsesShortDelayWithoutHeartbeatFlooding();
        heartbeatDoesNotConsumeControlPlanePerTarget();
        transientFailuresRemainDegradedAndRecover();
        verificationPausesClaimsAndPreservesCurrentTask();
        System.out.println("AutonomousTaskRunnerTest PASS");
    }

    private static void verificationPausesClaimsAndPreservesCurrentTask() throws Exception {
        DeviceTaskStore store = new DeviceTaskStore(new DeviceTaskStore.MemoryBackend());
        store.enqueue(new DeviceTaskStore.Task(
                "task-1", "lease-1", 7L, 9_000L, "pending", "{}"));
        FakeClient client = new FakeClient();
        AutonomousTaskRunner runner = new AutonomousTaskRunner(
                client, store, "round-1", 7L,
                task -> { throw new AccessibilityUiAdapter.UiException(
                        "verification_required"); });

        check(runner.runOnce(1_000L) == AutonomousTaskRunner.State.PAUSED,
                "verification pauses worker");
        check(store.next(7L, 1_000L).taskId.equals("task-1"),
                "verification preserves current task");
        int pulls = client.pulls;
        runner.runOnce(2_000L);
        check(client.pulls == pulls, "paused worker suppresses new claims");
        check(client.uploads == 0, "verification does not publish terminal result");
    }

    private static void appliesPacingAfterACompletedTarget() throws Exception {
        DeviceTaskStore store = new DeviceTaskStore(new DeviceTaskStore.MemoryBackend());
        store.enqueue(new DeviceTaskStore.Task(
                "task-1", "lease-1", 7L, 9_000L, "pending", "{}"));
        FakeClient client = new FakeClient();
        final long[] paced = {0L};
        AutonomousTaskRunner runner = new AutonomousTaskRunner(
                client, store, "round-1", 7L,
                task -> new DeviceTaskStore.Result("result-1", task.taskId, "{}"),
                completedTargets -> paced[0] = completedTargets);

        runner.runOnce(1_000L);

        check(paced[0] == 1L, "pacing follows durable task completion");
        check(client.uploads == 1, "result uploads before the next target");
    }

    private static void executesOnePersistedTaskAndQueuesResult() throws Exception {
        DeviceTaskStore store = new DeviceTaskStore(new DeviceTaskStore.MemoryBackend());
        store.enqueue(new DeviceTaskStore.Task(
                "task-1", "lease-1", 7L, 9_000L, "pending", "{}"));
        FakeClient client = new FakeClient();
        AutonomousTaskRunner runner = new AutonomousTaskRunner(
                client, store, "round-1", 7L,
                task -> new DeviceTaskStore.Result("result-1", task.taskId, "{}"));

        runner.runOnce(1_000L);

        check(store.next(7L, 1_000L) == null, "completed task removed");
        check(store.pendingResults().isEmpty(), "result uploaded in the same cycle");
        check(client.uploads == 1, "same-cycle result upload avoids one-second latency");
    }

    private static void activeQueueUsesShortDelayWithoutHeartbeatFlooding()
            throws Exception {
        DeviceTaskStore store = new DeviceTaskStore(new DeviceTaskStore.MemoryBackend());
        FakeClient client = new FakeClient();
        client.tasks.add(new DeviceTaskStore.Task(
                "task-1", "lease-1", 7L, 9_000L, "pending", "{}"));
        AutonomousTaskRunner runner = new AutonomousTaskRunner(
                client, store, "round-1", 7L);

        runner.runOnce(1_000L);
        runner.runOnce(1_100L);

        check(runner.recommendedDelayMs() == 0L, "queued work continues immediately");
        check(client.heartbeats == 1, "heartbeats remain throttled during short cycles");
    }

    private static void heartbeatDoesNotConsumeControlPlanePerTarget() throws Exception {
        FakeClient client = new FakeClient();
        AutonomousTaskRunner runner = new AutonomousTaskRunner(
                client, new DeviceTaskStore(new DeviceTaskStore.MemoryBackend()),
                "round-1", 7L);

        runner.runOnce(1_000L);
        runner.runOnce(60_999L);
        check(client.heartbeats == 1, "heartbeat stays quiet for one minute");
        runner.runOnce(61_000L);
        check(client.heartbeats == 2, "heartbeat resumes at one minute");
    }

    private static void pullsTasksAndFlushesIdempotentOutbox() throws Exception {
        DeviceTaskStore store = new DeviceTaskStore(new DeviceTaskStore.MemoryBackend());
        store.enqueueResult(new DeviceTaskStore.Result("result-1", "task-old", "{}"));
        FakeClient client = new FakeClient();
        client.tasks.add(new DeviceTaskStore.Task(
                "task-1", "lease-1", 7L, 9_000L, "pending", "{}"));
        AutonomousTaskRunner runner = new AutonomousTaskRunner(client, store, "round-1", 7L);

        AutonomousTaskRunner.State state = runner.runOnce(1_000L);

        check(state == AutonomousTaskRunner.State.HEALTHY, "healthy state");
        check(client.heartbeats == 1, "heartbeat sent");
        check(client.uploads == 1, "outbox uploaded");
        check(client.lastPullLimit == 1,
                "single-task queue preserves profile-to-action locality");
        check(store.pendingResults().isEmpty(), "duplicate acknowledged");
        check(store.next(7L, 1_000L).taskId.equals("task-1"), "task persisted");
    }

    private static void transientFailuresRemainDegradedAndRecover() throws Exception {
        FakeClient client = new FakeClient();
        client.fail = true;
        AutonomousTaskRunner runner = new AutonomousTaskRunner(
                client, new DeviceTaskStore(new DeviceTaskStore.MemoryBackend()),
                "round-1", 7L);

        check(runner.runOnce(1_000L) == AutonomousTaskRunner.State.DEGRADED,
                "first failure degraded");
        check(runner.runOnce(2_000L) == AutonomousTaskRunner.State.DEGRADED,
                "repeated transient failure stays retryable");
        client.fail = false;
        check(runner.runOnce(3_000L) == AutonomousTaskRunner.State.HEALTHY,
                "network recovery resumes work");
        check(client.pulls == 1, "claims resume after recovery");
    }

    private static final class FakeClient implements AutonomousTaskRunner.Client {
        final List<DeviceTaskStore.Task> tasks = new ArrayList<DeviceTaskStore.Task>();
        int heartbeats;
        int uploads;
        int pulls;
        int lastPullLimit;
        boolean fail;

        @Override
        public void heartbeat(String appVersion, String phase, int queueDepth, long nowMs)
                throws Exception {
            heartbeats++;
            if (fail) throw new Exception("network");
        }

        @Override
        public List<DeviceTaskStore.Task> pull(String roundId, int limit) {
            pulls++;
            lastPullLimit = limit;
            return tasks;
        }

        @Override
        public String uploadResult(DeviceTaskStore.Result result) {
            uploads++;
            return "duplicate";
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
