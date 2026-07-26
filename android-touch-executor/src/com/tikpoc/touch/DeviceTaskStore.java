package com.tikpoc.touch;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Durable-queue contract; Android supplies the persistent backend at runtime. */
public final class DeviceTaskStore {
    public interface Backend {
        void saveTask(Task task);
        List<Task> loadTasks();
        void deleteTask(String taskId);
        void saveResult(Result result);
        List<Result> loadResults();
        void deleteResult(String idempotencyKey);
        void clear();
    }

    public static final class Task {
        public final String taskId;
        public final String leaseId;
        public final long sessionEpoch;
        public final long leaseExpiresAtMs;
        public final String phase;
        public final String payload;

        public Task(String taskId, String leaseId, long sessionEpoch,
                long leaseExpiresAtMs, String phase, String payload) {
            if (taskId == null || taskId.trim().isEmpty() || leaseId == null
                    || leaseId.trim().isEmpty() || sessionEpoch <= 0
                    || leaseExpiresAtMs < 0 || phase == null || payload == null) {
                throw new IllegalArgumentException("invalid mobile task");
            }
            this.taskId = taskId;
            this.leaseId = leaseId;
            this.sessionEpoch = sessionEpoch;
            this.leaseExpiresAtMs = leaseExpiresAtMs;
            this.phase = phase;
            this.payload = payload;
        }

        public Task withPhase(String nextPhase) {
            return new Task(taskId, leaseId, sessionEpoch, leaseExpiresAtMs,
                    nextPhase, payload);
        }
    }

    public static final class Result {
        public final String idempotencyKey;
        public final String taskId;
        public final String payload;

        public Result(String idempotencyKey, String taskId, String payload) {
            if (idempotencyKey == null || idempotencyKey.trim().isEmpty()
                    || taskId == null || taskId.trim().isEmpty() || payload == null) {
                throw new IllegalArgumentException("invalid mobile result");
            }
            this.idempotencyKey = idempotencyKey;
            this.taskId = taskId;
            this.payload = payload;
        }
    }

    private final Backend backend;

    public DeviceTaskStore(Backend backend) {
        if (backend == null) throw new IllegalArgumentException("backend required");
        this.backend = backend;
    }

    public synchronized void enqueue(Task task) {
        for (Task existing : backend.loadTasks()) {
            if (existing.taskId.equals(task.taskId)) return;
        }
        backend.saveTask(task);
    }

    public synchronized void removeTask(String taskId) {
        backend.deleteTask(taskId);
    }

    public synchronized Task next(long sessionEpoch, long nowMs) {
        for (Task task : backend.loadTasks()) {
            if (task.sessionEpoch == sessionEpoch && task.leaseExpiresAtMs > nowMs) {
                return task;
            }
        }
        return null;
    }

    public synchronized void checkpoint(String taskId, String phase) {
        for (Task task : backend.loadTasks()) {
            if (task.taskId.equals(taskId)) {
                backend.saveTask(task.withPhase(phase));
                return;
            }
        }
        throw new IllegalArgumentException("task not found");
    }

    public synchronized void enqueueResult(Result result) {
        for (Result existing : backend.loadResults()) {
            if (existing.idempotencyKey.equals(result.idempotencyKey)) return;
        }
        backend.saveResult(result);
    }

    public synchronized List<Result> pendingResults() {
        return new ArrayList<Result>(backend.loadResults());
    }

    public synchronized void acknowledgeResult(String idempotencyKey) {
        backend.deleteResult(idempotencyKey);
    }

    public synchronized void clear() {
        backend.clear();
    }

    public synchronized int queueDepth(long sessionEpoch, long nowMs) {
        int count = 0;
        for (Task task : backend.loadTasks()) {
            if (task.sessionEpoch == sessionEpoch && task.leaseExpiresAtMs > nowMs) count++;
        }
        return count;
    }

    public static final class MemoryBackend implements Backend {
        private final Map<String, Task> tasks = new LinkedHashMap<String, Task>();
        private final Map<String, Result> results = new LinkedHashMap<String, Result>();

        @Override
        public void saveTask(Task task) { tasks.put(task.taskId, task); }

        @Override
        public List<Task> loadTasks() { return new ArrayList<Task>(tasks.values()); }

        @Override
        public void deleteTask(String taskId) { tasks.remove(taskId); }

        @Override
        public void saveResult(Result result) { results.put(result.idempotencyKey, result); }

        @Override
        public List<Result> loadResults() { return new ArrayList<Result>(results.values()); }

        @Override
        public void deleteResult(String idempotencyKey) { results.remove(idempotencyKey); }

        @Override
        public void clear() {
            tasks.clear();
            results.clear();
        }
    }
}
