package com.tikpoc.touch;

import java.util.List;

public final class AutonomousTaskRunner {
    private static final int MAX_QUEUE_DEPTH = 1;
    private static final long HEARTBEAT_INTERVAL_MS = 5_000L;
    private static final long ACTIVE_DELAY_MS = 100L;
    private static final long IDLE_DELAY_MS = 1_000L;
    public interface Client {
        void heartbeat(String appVersion, String phase, int queueDepth, long nowMs)
                throws Exception;
        List<DeviceTaskStore.Task> pull(String roundId, int limit) throws Exception;
        String uploadResult(DeviceTaskStore.Result result) throws Exception;
    }

    public interface Executor {
        DeviceTaskStore.Result execute(DeviceTaskStore.Task task) throws Exception;
    }

    public interface Pacing {
        void afterTarget(long completedTargets) throws Exception;
    }

    public enum State { HEALTHY, DEGRADED, PAUSED }

    private final Client client;
    private final DeviceTaskStore store;
    private final String roundId;
    private final long sessionEpoch;
    private final Executor executor;
    private final Pacing pacing;
    private long completedTargets;
    private int consecutiveFailures;
    private State state = State.HEALTHY;
    private long lastHeartbeatAtMs = -1L;
    private long recommendedDelayMs = IDLE_DELAY_MS;

    public AutonomousTaskRunner(Client client, DeviceTaskStore store,
            String roundId, long sessionEpoch) {
        this(client, store, roundId, sessionEpoch, null, completed -> {});
    }

    public AutonomousTaskRunner(Client client, DeviceTaskStore store,
            String roundId, long sessionEpoch, Executor executor) {
        this(client, store, roundId, sessionEpoch, executor, completed -> {});
    }

    public AutonomousTaskRunner(Client client, DeviceTaskStore store,
            String roundId, long sessionEpoch, Executor executor, Pacing pacing) {
        if (client == null || store == null || roundId == null || roundId.trim().isEmpty()
                || sessionEpoch <= 0 || pacing == null)
            throw new IllegalArgumentException("invalid runner");
        this.client = client;
        this.store = store;
        this.roundId = roundId;
        this.sessionEpoch = sessionEpoch;
        this.executor = executor;
        this.pacing = pacing;
    }

    public synchronized State runOnce(long nowMs) {
        if (state == State.PAUSED) return state;
        try {
            flushResults();
            int depth = store.queueDepth(sessionEpoch, nowMs);
            if (lastHeartbeatAtMs < 0L
                    || nowMs - lastHeartbeatAtMs >= HEARTBEAT_INTERVAL_MS) {
                client.heartbeat("1.0.0", "idle", depth, nowMs);
                lastHeartbeatAtMs = nowMs;
            }
            if (depth == 0) {
                pullIntoQueue(MAX_QUEUE_DEPTH);
                depth = store.queueDepth(sessionEpoch, nowMs);
            }
            if (executor != null) {
                DeviceTaskStore.Task task = store.next(sessionEpoch, nowMs);
                if (task != null) {
                    DeviceTaskStore.Result result = executor.execute(task);
                    store.enqueueResult(result);
                    store.removeTask(task.taskId);
                    flushResults();
                    completedTargets++;
                    pacing.afterTarget(completedTargets);
                    depth = store.queueDepth(sessionEpoch, nowMs);
                }
            }
            if (executor != null && depth < MAX_QUEUE_DEPTH) {
                pullIntoQueue(MAX_QUEUE_DEPTH - depth);
            }
            recommendedDelayMs = store.queueDepth(sessionEpoch, nowMs) > 0
                    ? ACTIVE_DELAY_MS : IDLE_DELAY_MS;
            consecutiveFailures = 0;
            state = State.HEALTHY;
        } catch (Exception error) {
            consecutiveFailures++;
            recommendedDelayMs = IDLE_DELAY_MS;
            state = State.DEGRADED;
        }
        return state;
    }

    public synchronized long recommendedDelayMs() {
        return recommendedDelayMs;
    }

    private void pullIntoQueue(int limit) throws Exception {
        if (limit <= 0) return;
        for (DeviceTaskStore.Task task : client.pull(roundId, limit)) {
            if (task.sessionEpoch != sessionEpoch) throw new Exception("stale task");
            store.enqueue(task);
        }
    }

    private void flushResults() throws Exception {
        for (DeviceTaskStore.Result result : store.pendingResults()) {
            String outcome = client.uploadResult(result);
            if ("accepted".equals(outcome) || "duplicate".equals(outcome)) {
                store.acknowledgeResult(result.idempotencyKey);
            }
        }
    }
}
