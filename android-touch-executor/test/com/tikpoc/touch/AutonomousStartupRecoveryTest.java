package com.tikpoc.touch;

public final class AutonomousStartupRecoveryTest {
    public static void main(String[] args) throws Exception {
        retriesTransientHomeRecovery();
        stopsAfterVerificationRecoveryAttempt();
        stopsAfterPartialVerificationReset();
        stopsAfterBoundedTransientAttempts();
        System.out.println("AutonomousStartupRecoveryTest PASS");
    }

    private static void retriesTransientHomeRecovery() throws Exception {
        int[] attempts = {0};
        int[] sleeps = {0};
        AutonomousStartupRecovery.Outcome outcome = AutonomousStartupRecovery.run(
                () -> {
                    attempts[0]++;
                    if (attempts[0] < 3) {
                        throw new AccessibilityUiAdapter.UiException("home_recovery_failed");
                    }
                },
                delayMs -> sleeps[0]++, 3, 500L);

        check(outcome == AutonomousStartupRecovery.Outcome.READY, "eventual recovery");
        check(attempts[0] == 3, "three recovery attempts");
        check(sleeps[0] == 2, "delay only between attempts");
    }

    private static void stopsAfterVerificationRecoveryAttempt() throws Exception {
        int[] attempts = {0};
        int[] sleeps = {0};
        AutonomousStartupRecovery.Outcome outcome = AutonomousStartupRecovery.run(
                () -> {
                    attempts[0]++;
                    throw new AccessibilityUiAdapter.UiException("verification_required");
                },
                delayMs -> sleeps[0]++, 3, 500L);

        check(outcome == AutonomousStartupRecovery.Outcome.VERIFICATION_REQUIRED,
                "verification outcome");
        check(attempts[0] == 1, "verification is not repeated");
        check(sleeps[0] == 0, "verification has no retry delay");
    }

    private static void stopsAfterPartialVerificationReset() throws Exception {
        int[] attempts = {0};
        AutonomousStartupRecovery.Outcome outcome = AutonomousStartupRecovery.run(
                () -> {
                    attempts[0]++;
                    throw new AccessibilityUiAdapter.UiException(
                            "verification_reset_failed");
                },
                delayMs -> {}, 3, 500L);

        check(outcome == AutonomousStartupRecovery.Outcome.VERIFICATION_REQUIRED,
                "partial verification reset remains blocked");
        check(attempts[0] == 1, "partial reset is not repeated");
    }

    private static void stopsAfterBoundedTransientAttempts() throws Exception {
        int[] attempts = {0};
        AutonomousStartupRecovery.Outcome outcome = AutonomousStartupRecovery.run(
                () -> {
                    attempts[0]++;
                    throw new AccessibilityUiAdapter.UiException("home_recovery_failed");
                },
                delayMs -> {}, 3, 500L);

        check(outcome == AutonomousStartupRecovery.Outcome.EXHAUSTED,
                "bounded exhaustion");
        check(attempts[0] == 3, "bounded attempt count");
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
