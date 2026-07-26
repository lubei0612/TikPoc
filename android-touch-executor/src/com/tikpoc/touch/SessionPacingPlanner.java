package com.tikpoc.touch;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public final class SessionPacingPlanner {
    public static final class Plan {
        public final long delayMs;
        public final int segmentSize;
        public final boolean homeBrowseDue;

        Plan(long delayMs, int segmentSize, boolean homeBrowseDue) {
            this.delayMs = delayMs;
            this.segmentSize = segmentSize;
            this.homeBrowseDue = homeBrowseDue;
        }
    }

    private SessionPacingPlanner() {}

    public static Plan plan(String deviceId, long completedTargets) throws Exception {
        if (deviceId == null || deviceId.trim().isEmpty() || completedTargets < 0L) {
            throw new IllegalArgumentException("pacing inputs are invalid");
        }
        byte[] deviceDigest = digest(deviceId.trim());
        int segmentSize = 40 + unsigned(deviceDigest[0]) % 41;
        byte[] progressDigest = digest(deviceId.trim() + ":" + completedTargets);
        long delayMs = 200L
                + (((long) unsigned(progressDigest[0]) << 8)
                + unsigned(progressDigest[1])) % 1_001L;
        boolean homeBrowseDue = completedTargets > 0L
                && completedTargets % segmentSize == 0L;
        return new Plan(delayMs, segmentSize, homeBrowseDue);
    }

    private static byte[] digest(String value) throws Exception {
        return MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8));
    }

    private static int unsigned(byte value) {
        return value & 0xff;
    }
}
