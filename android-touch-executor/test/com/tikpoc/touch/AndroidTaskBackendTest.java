package com.tikpoc.touch;

public final class AndroidTaskBackendTest {
    public static void main(String[] args) {
        String[] schema = AndroidTaskBackend.schema();
        check(schema.length == 2, "two queue tables");
        check(schema[0].contains("task_id TEXT PRIMARY KEY"), "task id unique");
        check(schema[1].contains("idempotency_key TEXT PRIMARY KEY"), "result id unique");
        System.out.println("AndroidTaskBackendTest PASS");
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
