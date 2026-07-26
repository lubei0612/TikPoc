package com.tikpoc.touch;

import java.util.List;

public final class AutonomousTaskRunner {
    public interface Client {
        void heartbeat(String appVersion, String phase, int queueDepth, long nowMs)
                throws Exception;
        List<DeviceTaskStore.Task> pull(String roundId, int limit) throws Exception;
        String uploadResult(DeviceTaskStore.Result result) throws Exception;
    }

    public interface Executor {
        DeviceTaskStore.Result execute(DeviceTaskStore.Task task) throws Exception;
    }

    public enum State { HEALTHY, DEGRADED, PAUSED }

    private final Client client;
    private final DeviceTaskStore store;
    private final String roundId;
    private final long sessionEpoch;
    private final Executor executor;
    private int consecutiveFailures;
    private State state = State.HEALTHY;

    public AutonomousTaskRunner(Client client, DeviceTaskStore store,
            String roundId, long sessionEpoch) {
        this(client, store, roundId, sessionEpoch, null);
    }

    public AutonomousTaskRunner(Client client, DeviceTaskStore store,
            String roundId, long sessionEpoch, Executor executor) {
        if (client == null || store == null || roundId == null || roundId.trim().isEmpty()
                || sessionEpoch <= 0) throw new IllegalArgumentException("invalid runner");
        this.client = client;
        this.store = store;
        this.roundId = roundId;
        this.sessionEpoch = sessionEpoch;
        this.executor = executor;
    }

    public synchronized State runOnce(long nowMs) {
        if (state == State.PAUSED) return state;
        try {
            flushResults();
            int depth = store.queueDepth(sessionEpoch, nowMs);
            client.heartbeat("1.0.0", "idle", depth, nowMs);
            if (executor != null) {
                DeviceTaskStore.Task task = store.next(sessionEpoch, nowMs);
                if (task != null) {
                    DeviceTaskStore.Result result = executor.execute(task);
                    store.enqueueResult(result);
                    store.removeTask(task.taskId);
                    depth = store.queueDepth(sessionEpoch, nowMs);
                }
            }
            if (depth < 20) {
                for (DeviceTaskStore.Task task : client.pull(roundId, 20 - depth)) {
                    if (task.sessionEpoch != sessionEpoch) throw new Exception("stale task");
                    store.enqueue(task);
                }
            }
            consecutiveFailures = 0;
            state = State.HEALTHY;
        } catch (Exception error) {
            consecutiveFailures++;
            state = consecutiveFailures >= 2 ? State.PAUSED : State.DEGRADED;
        }
        return state;
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
