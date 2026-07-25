package com.tikpoc.touch;

import java.util.Collections;

public final class ProtocolTest {
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    public static void main(String[] args) throws Exception {
        parsesCompleteRequest();
        acceptsEveryWorkerPhase();
        rejectsExpiredDeadline();
        rejectsOversizedRequest();
        rejectsUnknownCommand();
        encodesCompleteResponseContext();
        System.out.println("ProtocolTest PASS");
    }

    private static void parsesCompleteRequest() throws Exception {
        Protocol.Request request = Protocol.parseRequest(validRequest(), 1_000L);
        check(request.version == 1, "version");
        check(request.commandId.equals("cmd-1"), "command id");
        check(request.command.equals("health"), "command");
        check(request.deviceId.equals("device-1"), "device id");
        check(request.accountId.equals("account-1"), "account id");
        check(request.fenceToken == 7L, "fence token");
        check(request.assignmentId == 19L, "assignment id");
        check(request.phase.equals("profile_opening"), "phase");
        check(request.deadlineElapsedMs == 9_000L, "deadline");
        check(request.arguments.isEmpty(), "arguments");
    }

    private static void acceptsEveryWorkerPhase() throws Exception {
        String[] phases = new String[] {
                "pending", "profile_opening", "identity_confirmed", "waiting_snapshot",
                "video_opening", "video_confirmed", "quota_reserved", "action_executing",
                "action_reconciling", "deferred", "skipped", "completed"
        };
        for (String phase : phases) {
            Protocol.Request request = Protocol.parseRequest(
                    validRequest().replace("profile_opening", phase), 1_000L);
            check(request.phase.equals(phase), "worker phase " + phase);
        }
    }

    private static void rejectsExpiredDeadline() {
        expectFailure(
                () -> Protocol.parseRequest(
                        validRequest().replace("9000", "999"), 1_000L),
                "deadline_expired");
    }

    private static void rejectsOversizedRequest() {
        expectFailure(
                () -> Protocol.parseRequest(repeat("x", 262_145), 1_000L),
                "request_too_large");
    }

    private static void rejectsUnknownCommand() {
        expectFailure(
                () -> Protocol.parseRequest(
                        validRequest().replace("health", "delete_everything"),
                        1_000L),
                "unsupported_command");
    }

    private static void encodesCompleteResponseContext() throws Exception {
        Protocol.Request request = Protocol.parseRequest(validRequest(), 1_000L);
        Protocol.Response response = Protocol.Response.success(
                request,
                31L,
                "com.zhiliaoapp.musically",
                "com.ss.android.ugc.aweme.main.MainActivity",
                44L,
                "sha256:abc",
                Collections.<String, Object>singletonMap("ready", true))
                .withPerformance(7L, 18L);
        String encoded = Protocol.encodeResponse(response);
        check(encoded.contains("\"helper_version\":\"1.0.0\""), "helper version");
        check(encoded.contains("\"command_id\":\"cmd-1\""), "response command id");
        check(encoded.contains("\"fence_token\":7"), "response fence");
        check(encoded.contains("\"assignment_id\":19"), "response assignment");
        check(encoded.contains("\"event_sequence\":44"), "event sequence");
        check(encoded.contains("\"evidence_digest\":\"sha256:abc\""), "digest");
        check(encoded.contains("\"tree_age_ms\":7"), "tree age");
        check(encoded.contains("\"event_wait_ms\":18"), "event wait");
    }

    private static String validRequest() {
        return "{\"version\":1,\"command_id\":\"cmd-1\","
                + "\"command\":\"health\",\"device_id\":\"device-1\","
                + "\"account_id\":\"account-1\",\"fence_token\":7,"
                + "\"assignment_id\":19,\"phase\":\"profile_opening\","
                + "\"deadline_elapsed_ms\":9000,\"arguments\":{}}";
    }

    private static String repeat(String value, int count) {
        StringBuilder result = new StringBuilder(count);
        for (int index = 0; index < count; index++) {
            result.append(value);
        }
        return result.toString();
    }

    private static void expectFailure(ThrowingRunnable operation, String code) {
        try {
            operation.run();
            throw new AssertionError("expected failure " + code);
        } catch (Protocol.ProtocolException error) {
            check(error.code.equals(code), "failure code " + code);
        } catch (Exception error) {
            throw new AssertionError("unexpected exception", error);
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) {
            throw new AssertionError(label);
        }
    }
}
