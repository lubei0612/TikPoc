package com.tikpoc.touch;

public final class SessionPacingPlannerTest {
    public static void main(String[] args) throws Exception {
        plansAreBoundedAndDeterministic();
        deviceSchedulesDiffer();
        boundariesRequirePositiveSegmentMultiples();
        System.out.println("SessionPacingPlannerTest PASS");
    }

    private static void plansAreBoundedAndDeterministic() throws Exception {
        SessionPacingPlanner.Plan first = SessionPacingPlanner.plan("device-1", 17L);
        SessionPacingPlanner.Plan repeated = SessionPacingPlanner.plan("device-1", 17L);
        check(first.delayMs == repeated.delayMs, "delay deterministic");
        check(first.segmentSize == repeated.segmentSize, "segment deterministic");
        check(first.delayMs >= 200L && first.delayMs <= 1_200L, "delay bounded");
        check(first.segmentSize >= 40 && first.segmentSize <= 80, "segment bounded");
    }

    private static void deviceSchedulesDiffer() throws Exception {
        SessionPacingPlanner.Plan first = SessionPacingPlanner.plan("device-1", 17L);
        SessionPacingPlanner.Plan second = SessionPacingPlanner.plan("device-2", 17L);
        check(first.delayMs != second.delayMs || first.segmentSize != second.segmentSize,
                "device schedules differ");
    }

    private static void boundariesRequirePositiveSegmentMultiples() throws Exception {
        SessionPacingPlanner.Plan seed = SessionPacingPlanner.plan("device-1", 0L);
        check(!seed.homeBrowseDue, "zero is not a boundary");
        SessionPacingPlanner.Plan due = SessionPacingPlanner.plan(
                "device-1", seed.segmentSize);
        check(due.homeBrowseDue, "segment multiple is due");
        SessionPacingPlanner.Plan next = SessionPacingPlanner.plan(
                "device-1", seed.segmentSize + 1L);
        check(!next.homeBrowseDue, "nonmultiple is not due");
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
