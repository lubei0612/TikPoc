package com.tikpoc.touch;

public final class AutonomousStartupRecovery {
    public enum Outcome {
        READY,
        VERIFICATION_REQUIRED,
        EXHAUSTED
    }

    public interface Operation {
        void run() throws Exception;
    }

    public interface Sleeper {
        void sleep(long delayMs) throws InterruptedException;
    }

    private AutonomousStartupRecovery() {}

    public static Outcome run(
            Operation operation, Sleeper sleeper, int maxAttempts, long retryDelayMs)
            throws InterruptedException {
        if (operation == null || sleeper == null || maxAttempts <= 0 || retryDelayMs < 0L) {
            throw new IllegalArgumentException("valid startup recovery configuration required");
        }
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                operation.run();
                return Outcome.READY;
            } catch (AccessibilityUiAdapter.UiException error) {
                if (error.code != null && error.code.startsWith("verification_")) {
                    return Outcome.VERIFICATION_REQUIRED;
                }
            } catch (InterruptedException interrupted) {
                throw interrupted;
            } catch (Exception transientFailure) {
                // A not-yet-stable TikTok tree is retried below.
            }
            if (attempt < maxAttempts) sleeper.sleep(retryDelayMs);
        }
        return Outcome.EXHAUSTED;
    }
}
