package com.tikpoc.touch;

import java.util.List;

public final class DeviceTaskStoreTest {
    public static void main(String[] args) throws Exception {
        skipsExpiredAndStaleTasks();
        checkpointsSurviveStoreRecreation();
        collapsesDuplicateOutboxResults();
        System.out.println("DeviceTaskStoreTest PASS");
    }

    private static void skipsExpiredAndStaleTasks() throws Exception {
        DeviceTaskStore.MemoryBackend backend = new DeviceTaskStore.MemoryBackend();
        DeviceTaskStore store = new DeviceTaskStore(backend);
        store.enqueue(task("expired", 7L, 100L));
        store.enqueue(task("stale", 6L, 1_000L));
        store.enqueue(task("ready", 7L, 1_000L));

        DeviceTaskStore.Task next = store.next(7L, 101L);

        check(next != null && next.taskId.equals("ready"), "only valid task selected");
    }

    private static void checkpointsSurviveStoreRecreation() throws Exception {
        DeviceTaskStore.MemoryBackend backend = new DeviceTaskStore.MemoryBackend();
        DeviceTaskStore first = new DeviceTaskStore(backend);
        first.enqueue(task("task-1", 7L, 1_000L));
        first.checkpoint("task-1", "video_confirmed");

        DeviceTaskStore restored = new DeviceTaskStore(backend);

        check(restored.next(7L, 1L).phase.equals("video_confirmed"), "phase restored");
    }

    private static void collapsesDuplicateOutboxResults() throws Exception {
        DeviceTaskStore store = new DeviceTaskStore(new DeviceTaskStore.MemoryBackend());

        store.enqueueResult(new DeviceTaskStore.Result("result-1", "task-1", "{}"));
        store.enqueueResult(new DeviceTaskStore.Result("result-1", "task-1", "{}"));
        List<DeviceTaskStore.Result> pending = store.pendingResults();

        check(pending.size() == 1, "one durable result");
    }

    private static DeviceTaskStore.Task task(String id, long epoch, long expiry) {
        return new DeviceTaskStore.Task(
                id, "lease-" + id, epoch, expiry, "pending", "{}");
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
