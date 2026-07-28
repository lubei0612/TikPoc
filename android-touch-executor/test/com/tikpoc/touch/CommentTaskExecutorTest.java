package com.tikpoc.touch;

import java.util.ArrayList;
import java.util.List;

public final class CommentTaskExecutorTest {
    public static void main(String[] args) throws Exception {
        checkpointsBeforeSingleSubmitAndConfirmsVisibleText();
        uncertainContinuationOnlyObserves();
        transportLossAfterSubmitBecomesReadOnlyReconciliation();
        verificationResetsBeforeMutationWhenHomeIsVerified();
        verificationPausesWhenResetDoesNotClearChallenge();
        System.out.println("CommentTaskExecutorTest PASS");
    }

    private static void checkpointsBeforeSingleSubmitAndConfirmsVisibleText()
            throws Exception {
        FakeUi ui = new FakeUi();
        FakeCheckpoints checkpoints = new FakeCheckpoints();
        CommentTaskExecutor executor = new CommentTaskExecutor(ui, checkpoints);

        CommentTaskExecutor.Result result = executor.execute(task("video_opening"));

        check(result.state.equals("visible_confirmed"), "visible comment confirmed");
        check(ui.videoOpens == 1, "exact video opened once");
        check(ui.submits == 1, "comment submitted once");
        check(checkpoints.phases.get(0).equals("video_verified"),
                "video checkpoint precedes composer");
        check(checkpoints.phases.get(1).equals("comment_submitting"),
                "submit checkpoint precedes mutation");
        check(ui.browses == 1, "confirmed comment returns to bounded browsing");
    }

    private static void uncertainContinuationOnlyObserves() throws Exception {
        FakeUi ui = new FakeUi();
        ui.visible = false;
        CommentTaskExecutor executor = new CommentTaskExecutor(ui, new FakeCheckpoints());

        CommentTaskExecutor.Result result = executor.execute(task("comment_reconciling"));

        check(result.state.equals("uncertain"), "missing visible evidence stays uncertain");
        check(ui.videoOpens == 0 && ui.submits == 0, "reconciliation is read only");
        check(ui.observations == 1, "one reconciliation observation");
    }

    private static void transportLossAfterSubmitBecomesReadOnlyReconciliation()
            throws Exception {
        FakeUi ui = new FakeUi();
        ui.submitFailure = new Exception("transport");
        FakeCheckpoints checkpoints = new FakeCheckpoints();
        CommentTaskExecutor executor = new CommentTaskExecutor(ui, checkpoints);

        CommentTaskExecutor.Result result = executor.execute(task("video_opening"));

        check(result.state.equals("uncertain"), "post-submit loss is uncertain");
        check(ui.submits == 1, "transport loss does not submit twice");
        check(checkpoints.phases.contains("comment_reconciling"),
                "uncertain phase persisted");
    }

    private static void verificationResetsBeforeMutationWhenHomeIsVerified()
            throws Exception {
        FakeUi ui = new FakeUi();
        ui.interruption = "verification_required";
        CommentTaskExecutor executor = new CommentTaskExecutor(ui, new FakeCheckpoints());
        CommentTaskExecutor.Result result = executor.execute(task("video_opening"));

        check(result.state.equals("visible_confirmed"), "verification reset recovered");
        check(ui.verificationResets == 1, "verification reset performed once");
        check(ui.videoOpens == 1 && ui.submits == 1,
                "mutation resumes only after reset");
    }

    private static void verificationPausesWhenResetDoesNotClearChallenge() throws Exception {
        FakeUi ui = new FakeUi();
        ui.interruption = "verification_required";
        ui.resetLeavesVerification = true;
        CommentTaskExecutor executor = new CommentTaskExecutor(ui, new FakeCheckpoints());
        try {
            executor.execute(task("video_opening"));
            throw new AssertionError("verification accepted");
        } catch (AccessibilityUiAdapter.UiException error) {
            check(error.code.equals("verification_required"), "verification propagated");
        }
        check(ui.verificationResets == 1, "verification reset attempted once");
        check(ui.videoOpens == 0 && ui.submits == 0, "verification blocks mutations");
    }

    private static CommentTaskExecutor.Task task(String phase) {
        return new CommentTaskExecutor.Task(
                "comment:42", 42L, "7523456789012345678",
                "https://www.tiktok.com/@bag/video/7523456789012345678",
                "bag", "rare archive piece",
                "That structured shape changes the whole outfit ✨", phase);
    }

    private static final class FakeCheckpoints implements CommentTaskExecutor.Checkpoints {
        final List<String> phases = new ArrayList<String>();

        @Override
        public void save(String taskId, String phase) { phases.add(phase); }
    }

    private static final class FakeUi implements CommentTaskExecutor.Ui {
        int videoOpens;
        int submits;
        int observations;
        int browses;
        int recoveries;
        int verificationResets;
        boolean visible = true;
        String interruption = "none";
        boolean resetLeavesVerification;
        Exception submitFailure;

        @Override
        public String observeInterruption() { return interruption; }

        @Override
        public void openAndVerifyVideo(String videoId, String videoUrl,
                String creatorUsername, String captionAnchor) {
            check(creatorUsername.equals("bag"), "creator identity propagated");
            check(captionAnchor.equals("rare archive piece"), "caption anchor propagated");
            videoOpens++;
        }

        @Override
        public void submitFirstLevel(String text) throws Exception {
            submits++;
            if (submitFailure != null) throw submitFailure;
        }

        @Override
        public boolean observeSubmitted(String text) {
            observations++;
            return visible;
        }

        @Override
        public void recoverAndBrowseHome() {
            recoveries++;
            if ("verification_required".equals(interruption)) verificationResets++;
            browses++;
            if (!resetLeavesVerification) interruption = "none";
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
