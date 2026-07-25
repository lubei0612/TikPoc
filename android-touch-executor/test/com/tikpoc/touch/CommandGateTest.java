package com.tikpoc.touch;

import java.util.Collections;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

public final class CommandGateTest {
    public static void main(String[] args) throws Exception {
        replaysCompletedCommandWithoutExecutingAgain();
        sharesOneExecutionForConcurrentDuplicate();
        rejectsDifferentCommandWhileBusy();
        boundsAndExpiresCompletedCache();
        System.out.println("CommandGateTest PASS");
    }

    private static void replaysCompletedCommandWithoutExecutingAgain() throws Exception {
        MutableClock clock = new MutableClock();
        CommandGate gate = new CommandGate(clock);
        AtomicInteger calls = new AtomicInteger();
        Protocol.Request request = request("cmd-1");
        Protocol.Response first = gate.execute(request, operation(calls, request));
        Protocol.Response replay = gate.execute(request, failIfCalled());
        check(Protocol.encodeResponse(first).equals(Protocol.encodeResponse(replay)), "same result");
        check(calls.get() == 1, "single completed execution");
    }

    private static void sharesOneExecutionForConcurrentDuplicate() throws Exception {
        MutableClock clock = new MutableClock();
        CommandGate gate = new CommandGate(clock);
        AtomicInteger calls = new AtomicInteger();
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicReference<Protocol.Response> first = new AtomicReference<Protocol.Response>();
        AtomicReference<Protocol.Response> duplicate = new AtomicReference<Protocol.Response>();
        Protocol.Request request = request("cmd-duplicate");
        Thread owner = new Thread(() -> first.set(uncheckedExecute(gate, request, () -> {
            calls.incrementAndGet();
            entered.countDown();
            release.await();
            return success(request, 1);
        })));
        owner.start();
        entered.await();
        Thread waiter = new Thread(() -> duplicate.set(
                uncheckedExecute(gate, request, failIfCalled())));
        waiter.start();
        release.countDown();
        owner.join();
        waiter.join();
        check(calls.get() == 1, "single concurrent execution");
        check(Protocol.encodeResponse(first.get()).equals(
                Protocol.encodeResponse(duplicate.get())), "shared response");
    }

    private static void rejectsDifferentCommandWhileBusy() throws Exception {
        CommandGate gate = new CommandGate(new MutableClock());
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        Protocol.Request ownerRequest = request("cmd-owner");
        Thread owner = new Thread(() -> uncheckedExecute(gate, ownerRequest, () -> {
            entered.countDown();
            release.await();
            return success(ownerRequest, 1);
        }));
        owner.start();
        entered.await();
        Protocol.Response busy = gate.execute(request("cmd-other"), failIfCalled());
        check(busy.values.get("status").equals("error"), "busy status");
        @SuppressWarnings("unchecked")
        Map<String, Object> error = (Map<String, Object>) busy.values.get("error");
        check(error.get("code").equals("busy"), "busy code");
        release.countDown();
        owner.join();
    }

    private static void boundsAndExpiresCompletedCache() throws Exception {
        MutableClock clock = new MutableClock();
        CommandGate gate = new CommandGate(clock);
        for (int index = 0; index < 33; index++) {
            Protocol.Request request = request("cmd-" + index);
            gate.execute(request, operation(new AtomicInteger(), request));
        }
        check(gate.completedCount() == 32, "entry bound");
        clock.nowMs = 60_001L;
        Protocol.Request fresh = request("cmd-fresh");
        gate.execute(fresh, operation(new AtomicInteger(), fresh));
        check(gate.completedCount() == 1, "expiry");
        check(gate.completedBytes() <= 262_144, "byte bound");
    }

    private static CommandGate.Operation operation(
            AtomicInteger calls, Protocol.Request request) {
        return () -> {
            calls.incrementAndGet();
            return success(request, 1);
        };
    }

    private static CommandGate.Operation failIfCalled() {
        return () -> { throw new AssertionError("operation executed twice"); };
    }

    private static Protocol.Response success(Protocol.Request request, int value) {
        return Protocol.Response.success(
                request, 1L, "com.zhiliaoapp.musically", "MainActivity", 1L,
                "sha256:test", Collections.<String, Object>singletonMap("value", value));
    }

    private static Protocol.Request request(String commandId) throws Exception {
        String json = "{\"version\":1,\"command_id\":\"" + commandId + "\","
                + "\"command\":\"health\",\"device_id\":\"device-1\","
                + "\"account_id\":\"account-1\",\"fence_token\":7,"
                + "\"assignment_id\":19,\"phase\":\"profile_opening\","
                + "\"deadline_elapsed_ms\":900000,\"arguments\":{}}";
        return Protocol.parseRequest(json, 0L);
    }

    private static Protocol.Response uncheckedExecute(
            CommandGate gate, Protocol.Request request, CommandGate.Operation operation) {
        try {
            return gate.execute(request, operation);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private static final class MutableClock implements CommandGate.Clock {
        private long nowMs;

        @Override
        public long elapsedRealtimeMs() {
            return nowMs;
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
