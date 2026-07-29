package com.tikpoc.touch;

public final class CommentTaskExecutor {
    public interface Ui {
        String observeInterruption() throws Exception;
        void openAndVerifyVideo(String videoId, String videoUrl,
                String creatorUsername, String captionAnchor) throws Exception;
        void submitFirstLevel(String text) throws Exception;
        boolean observeSubmitted(String text) throws Exception;
        void recoverAndBrowseHome() throws Exception;
    }

    public interface Checkpoints {
        void save(String taskId, String phase) throws Exception;
    }

    public static final class Task {
        public final String taskId;
        public final long planId;
        public final String videoId;
        public final String videoUrl;
        public final String creatorUsername;
        public final String captionAnchor;
        public final String publishText;
        public final String phase;

        public Task(String taskId, long planId, String videoId, String videoUrl,
                String creatorUsername, String captionAnchor, String publishText,
                String phase) {
            if (empty(taskId) || planId <= 0L || empty(videoId) || empty(videoUrl)
                    || empty(publishText) || empty(phase)) {
                throw new IllegalArgumentException("invalid comment task");
            }
            this.taskId = taskId;
            this.planId = planId;
            this.videoId = videoId;
            this.videoUrl = videoUrl;
            this.creatorUsername = creatorUsername == null ? "" : creatorUsername.trim();
            this.captionAnchor = captionAnchor == null ? "" : captionAnchor.trim();
            this.publishText = publishText;
            this.phase = phase;
        }
    }

    public static final class Result {
        public final String state;
        public final String phase;
        public final String code;

        private Result(String state, String phase, String code) {
            this.state = state;
            this.phase = phase;
            this.code = code;
        }
    }

    private final Ui ui;
    private final Checkpoints checkpoints;

    public CommentTaskExecutor(Ui ui, Checkpoints checkpoints) {
        if (ui == null || checkpoints == null)
            throw new IllegalArgumentException("comment executor dependencies required");
        this.ui = ui;
        this.checkpoints = checkpoints;
    }

    public Result execute(Task task) throws Exception {
        requireNoVerification();
        if ("comment_reconciling".equals(task.phase)
                || "comment_submitting".equals(task.phase)) {
            return reconcile(task);
        }
        ui.openAndVerifyVideo(task.videoId, task.videoUrl,
                task.creatorUsername, task.captionAnchor);
        checkpoints.save(task.taskId, "video_verified");
        requireNoVerification();
        checkpoints.save(task.taskId, "comment_submitting");
        try {
            ui.submitFirstLevel(task.publishText);
        } catch (AccessibilityUiAdapter.UiException error) {
            if ("verification_required".equals(error.code)) throw error;
            checkpoints.save(task.taskId, "comment_reconciling");
            return new Result("uncertain", "comment_reconciling", error.code);
        } catch (Exception error) {
            checkpoints.save(task.taskId, "comment_reconciling");
            return new Result("uncertain", "comment_reconciling", "submit_transport_lost");
        }
        if (!ui.observeSubmitted(task.publishText)) {
            checkpoints.save(task.taskId, "comment_reconciling");
            return new Result("uncertain", "comment_reconciling", "visible_text_missing");
        }
        recoverAfterConfirmation();
        return new Result("visible_confirmed", "comment_reconciling", "visible_confirmed");
    }

    private Result reconcile(Task task) throws Exception {
        ui.openAndVerifyVideo(task.videoId, task.videoUrl,
                task.creatorUsername, task.captionAnchor);
        if (!ui.observeSubmitted(task.publishText)) {
            return new Result("uncertain", "comment_reconciling", "visible_text_missing");
        }
        recoverAfterConfirmation();
        return new Result("visible_confirmed", "comment_reconciling", "visible_confirmed");
    }

    private void recoverAfterConfirmation() {
        try {
            ui.recoverAndBrowseHome();
        } catch (Exception ignored) {
            // The durable visible-post evidence outranks best-effort navigation cleanup.
        }
    }

    private void requireNoVerification() throws Exception {
        if (TikTokInterruptionSemantics.VERIFICATION_REQUIRED.equals(
                ui.observeInterruption())) {
            ui.recoverAndBrowseHome();
            if (TikTokInterruptionSemantics.VERIFICATION_REQUIRED.equals(
                    ui.observeInterruption())) {
                throw new AccessibilityUiAdapter.UiException("verification_required");
            }
        }
    }

    private static boolean empty(String value) {
        return value == null || value.trim().isEmpty();
    }
}
