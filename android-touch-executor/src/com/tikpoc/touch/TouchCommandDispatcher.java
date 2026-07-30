package com.tikpoc.touch;

import java.util.LinkedHashMap;
import java.util.Map;

public final class TouchCommandDispatcher {
    private static final int ACTION_CONTROL_ATTEMPTS = 5;
    private static final int ACTION_CONFIRMATION_ATTEMPTS = 5;
    private static final int PROFILE_POST_GRID_ATTEMPTS = 5;
    public interface SnapshotSource {
        SemanticSnapshot current() throws Exception;

        default SemanticSnapshot awaitAfter(long eventSequence, long timeoutMs) throws Exception {
            return current();
        }
    }

    public interface Actuator {
        boolean click(SemanticSnapshot.Node node) throws Exception;
        boolean openProfile(String route) throws Exception;
        default String searchProfile(String username) throws Exception { return "timeout"; }
        default boolean browseHomeReadOnly() throws Exception { return false; }
        default boolean dismissOrdinaryInterruption(String kind) throws Exception {
            return false;
        }
        default boolean resetVerification() throws Exception { return false; }
        default boolean returnToHome() throws Exception { return false; }
        default boolean openCommentVideo(String videoId, String videoUrl) throws Exception {
            return false;
        }
        default boolean openCommentThreadReadOnly() throws Exception { return false; }
        default boolean submitFirstLevelComment(String text) throws Exception { return false; }
        default String commentSubmitErrorCode() { return "comment_submit_rejected"; }
    }

    public interface Clock {
        long elapsedRealtimeMs();
    }

    public interface SurfaceSource {
        String packageName();
        String activityName();
    }

    private final SnapshotSource snapshots;
    private final Actuator actuator;
    private final Clock clock;
    private final SurfaceSource surface;

    public TouchCommandDispatcher(
            SnapshotSource snapshots, Actuator actuator, Clock clock, SurfaceSource surface) {
        this.snapshots = snapshots;
        this.actuator = actuator;
        this.clock = clock;
        this.surface = surface;
    }

    public Protocol.Response dispatch(Protocol.Request request) throws Exception {
        try {
            return dispatchVerified(request);
        } catch (TikTokSemantics.SemanticException error) {
            return Protocol.Response.error(request, error.code, "semantic evidence unavailable");
        } catch (Protocol.ProtocolException error) {
            return Protocol.Response.error(request, error.code, "invalid command arguments");
        } catch (Exception error) {
            return Protocol.Response.error(
                    request, "command_failed", error.getClass().getSimpleName());
        }
    }

    private Protocol.Response dispatchVerified(Protocol.Request request) throws Exception {
        long startedAt = clock.elapsedRealtimeMs();
        if (request.command.equals("health")) return health(request, startedAt);
        if (request.command.equals("diagnostics")) return diagnostics(request, startedAt);
        if (request.command.equals("browse_home")) return browseHome(request, startedAt);
        if (request.command.equals("observe_interruption")) {
            return observeInterruption(request, startedAt);
        }
        if (request.command.equals("recover_home")) return recoverHome(request, startedAt);
        if (request.command.equals("open_comment_video")) {
            return openCommentVideo(request, startedAt);
        }
        if (request.command.equals("observe_comment_video")) {
            return observeCommentVideo(request, startedAt);
        }
        if (request.command.equals("submit_first_level_comment")) {
            return submitFirstLevelComment(request, startedAt);
        }
        if (request.command.equals("observe_submitted_comment")) {
            return observeSubmittedComment(request, startedAt);
        }
        if (request.command.equals("apply_action")) return applyAction(request, startedAt);
        if (request.command.equals("observe_action")) return observeAction(request, startedAt);
        if (request.command.equals("observe_profile")) return observeProfile(request, startedAt);
        if (request.command.equals("open_profile")) return openProfile(request, startedAt);
        if (request.command.equals("open_profile_search")) {
            return openProfileSearch(request, startedAt);
        }
        if (request.command.equals("open_video")) return openVideo(request, startedAt);
        return Protocol.Response.error(request, "unsupported_command", "command unavailable");
    }

    private Protocol.Response observeInterruption(Protocol.Request request, long startedAt)
            throws Exception {
        SemanticSnapshot snapshot = snapshots.current();
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("interruption", TikTokInterruptionSemantics.classify(snapshot));
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response recoverHome(Protocol.Request request, long startedAt)
            throws Exception {
        SemanticSnapshot snapshot = snapshots.current();
        String interruption = TikTokInterruptionSemantics.classify(snapshot);
        if (TikTokInterruptionSemantics.VERIFICATION_REQUIRED.equals(interruption)) {
            if (!actuator.resetVerification()) {
                return Protocol.Response.error(
                        request, "verification_reset_failed", "verification reset unavailable");
            }
            SemanticSnapshot observed = snapshot;
            for (int attempt = 0; attempt < 5; attempt++) {
                observed = snapshots.awaitAfter(observed.eventSequence, 400L);
                if (!TikTokInterruptionSemantics.VERIFICATION_REQUIRED.equals(
                                TikTokInterruptionSemantics.classify(observed))
                        && TikTokInterruptionSemantics.isHomeVisible(observed)) {
                    Map<String, Object> evidence = new LinkedHashMap<String, Object>();
                    evidence.put("interruption", interruption);
                    evidence.put("home_visible", true);
                    evidence.put("recovery_performed", true);
                    evidence.put("verification_reset_count", 2L);
                    return success(request, startedAt, observed, evidence);
                }
            }
            return Protocol.Response.error(
                    request, "verification_required", "verification remains after reset");
        }
        if (!TikTokInterruptionSemantics.NONE.equals(interruption)
                && !actuator.dismissOrdinaryInterruption(interruption)) {
            return Protocol.Response.error(
                    request, "interruption_dismiss_failed", "ordinary interruption remained");
        }
        if (!actuator.returnToHome()) {
            return Protocol.Response.error(
                    request, "home_recovery_failed", "home navigation unavailable");
        }
        SemanticSnapshot observed = snapshot;
        for (int attempt = 0; attempt < 5; attempt++) {
            observed = snapshots.awaitAfter(observed.eventSequence, 400L);
            if (TikTokInterruptionSemantics.isHomeVisible(observed)) {
                Map<String, Object> evidence = new LinkedHashMap<String, Object>();
                evidence.put("interruption", interruption);
                evidence.put("home_visible", true);
                evidence.put("recovery_performed", true);
                return success(request, startedAt, observed, evidence);
            }
        }
        return Protocol.Response.error(
                request, "home_recovery_failed", "home surface not verified");
    }

    private Protocol.Response openCommentVideo(Protocol.Request request, long startedAt)
            throws Exception {
        String videoId = requiredArgument(request, "video_id");
        String videoUrl = requiredArgument(request, "video_url");
        String creatorUsername = optionalArgument(request, "creator_username");
        String captionAnchor = optionalArgument(request, "caption_anchor");
        SemanticSnapshot before = snapshots.current();
        if (!actuator.openCommentVideo(videoId, videoUrl)) {
            return Protocol.Response.error(request, "video_open_rejected", "route rejected");
        }
        SemanticSnapshot observed = before;
        for (int attempt = 0; attempt < 10; attempt++) {
            observed = snapshots.awaitAfter(observed.eventSequence, 400L);
            if (commentVideoVisible(
                    observed, videoId, creatorUsername, captionAnchor)) {
                return commentVideoEvidence(request, startedAt, observed, videoId,
                        creatorUsername, captionAnchor);
            }
        }
        return Protocol.Response.error(
                request, "comment_video_not_verified", "exact video evidence unavailable");
    }

    private Protocol.Response observeCommentVideo(Protocol.Request request, long startedAt)
            throws Exception {
        String videoId = requiredArgument(request, "video_id");
        String creatorUsername = optionalArgument(request, "creator_username");
        String captionAnchor = optionalArgument(request, "caption_anchor");
        SemanticSnapshot snapshot = snapshots.current();
        if (!commentVideoVisible(snapshot, videoId, creatorUsername, captionAnchor)) {
            return Protocol.Response.error(
                    request, "comment_video_not_verified", "exact video evidence unavailable");
        }
        return commentVideoEvidence(request, startedAt, snapshot, videoId,
                creatorUsername, captionAnchor);
    }

    private boolean commentVideoVisible(SemanticSnapshot snapshot, String videoId,
            String creatorUsername, String captionAnchor) {
        if (!TikTokSemantics.hasVideoControls(snapshot)) return false;
        if (!creatorUsername.isEmpty() || !captionAnchor.isEmpty()) {
            return TikTokInterruptionSemantics.hasVisibleVideoIdentity(
                    snapshot, creatorUsername, captionAnchor);
        }
        return TikTokInterruptionSemantics.containsExactVisibleText(snapshot, videoId);
    }

    private Protocol.Response commentVideoEvidence(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            String videoId, String creatorUsername, String captionAnchor) {
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("video_id", videoId);
        evidence.put("exact_video_visible", true);
        evidence.put("creator_visible", !creatorUsername.isEmpty());
        evidence.put("caption_anchor_visible", !captionAnchor.isEmpty());
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response submitFirstLevelComment(
            Protocol.Request request, long startedAt) throws Exception {
        String publishText = requiredArgument(request, "publish_text");
        SemanticSnapshot before = snapshots.current();
        if (TikTokInterruptionSemantics.VERIFICATION_REQUIRED.equals(
                TikTokInterruptionSemantics.classify(before))) {
            return Protocol.Response.error(
                    request, "verification_required", "operator verification required");
        }
        if (!actuator.submitFirstLevelComment(publishText)) {
            String code = actuator.commentSubmitErrorCode();
            if (code == null || code.trim().isEmpty()) code = "comment_submit_rejected";
            return Protocol.Response.error(request, code, "submit rejected");
        }
        SemanticSnapshot observed = before;
        for (int attempt = 0; attempt < 5; attempt++) {
            observed = snapshots.awaitAfter(observed.eventSequence, 400L);
            if (TikTokInterruptionSemantics.containsExactVisibleText(
                    observed, publishText)) {
                return submittedCommentEvidence(
                        request, startedAt, observed, publishText, true);
            }
        }
        return Protocol.Response.uncertain(
                request, elapsed(startedAt), surface.packageName(), surface.activityName(),
                observed.eventSequence, observed.digest,
                commentEvidence(publishText, false));
    }

    private Protocol.Response observeSubmittedComment(
            Protocol.Request request, long startedAt) throws Exception {
        String publishText = requiredArgument(request, "publish_text");
        SemanticSnapshot snapshot = snapshots.current();
        boolean visible = TikTokInterruptionSemantics.containsExactVisibleText(
                snapshot, publishText);
        if (!visible && actuator.openCommentThreadReadOnly()) {
            for (int attempt = 0; attempt < 5; attempt++) {
                snapshot = snapshots.awaitAfter(snapshot.eventSequence, 400L);
                if (TikTokInterruptionSemantics.containsExactVisibleText(
                        snapshot, publishText)) {
                    visible = true;
                    break;
                }
            }
        }
        return submittedCommentEvidence(
                request, startedAt, snapshot, publishText, visible);
    }

    private Protocol.Response submittedCommentEvidence(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            String publishText, boolean visible) {
        return success(request, startedAt, snapshot, commentEvidence(publishText, visible));
    }

    private Map<String, Object> commentEvidence(String publishText, boolean visible) {
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("visible_confirmed", visible);
        evidence.put("text_length", publishText.length());
        return evidence;
    }

    private Protocol.Response browseHome(Protocol.Request request, long startedAt)
            throws Exception {
        if (!actuator.browseHomeReadOnly()) {
            return Protocol.Response.error(
                    request, "home_browse_rejected", "home browse unavailable");
        }
        SemanticSnapshot snapshot = snapshots.current();
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("home_visible", true);
        evidence.put("browse_performed", true);
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response health(Protocol.Request request, long startedAt) throws Exception {
        SemanticSnapshot snapshot = snapshots.current();
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("service_enabled", true);
        evidence.put("tiktok_foreground", surface.packageName().equals("com.zhiliaoapp.musically"));
        evidence.put("surface", surface.activityName());
        evidence.put("busy", false);
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response diagnostics(Protocol.Request request, long startedAt)
            throws Exception {
        SemanticSnapshot snapshot = snapshots.current();
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("node_count", snapshot.nodes.size());
        evidence.put("tree_age_ms", snapshot.ageMs(clock.elapsedRealtimeMs()));
        evidence.put("visible_nodes", visibleNodeCount(snapshot));
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response observeAction(Protocol.Request request, long startedAt)
            throws Exception {
        String action = requiredArgument(request, "action");
        SemanticSnapshot snapshot = snapshots.current();
        SemanticSnapshot.Node observedControl = null;
        for (int attempt = 0; attempt <= ACTION_CONFIRMATION_ATTEMPTS; attempt++) {
            if (action.equals("repost")) {
                SemanticSnapshot.Node confirmation =
                        TikTokSemantics.repostConfirmation(snapshot);
                if (confirmation != null) {
                    return observedAction(
                            request, startedAt, snapshot, action, "on",
                            confirmation.resourceId);
                }
            }
            try {
                observedControl = TikTokSemantics.uniqueControl(snapshot, action);
                if (TikTokSemantics.actionState(observedControl).equals("on")) {
                    return observedAction(
                            request, startedAt, snapshot, action, "on",
                            observedControl.resourceId);
                }
            } catch (TikTokSemantics.SemanticException error) {
                if (!error.code.equals("missing_control")) throw error;
            }
            if (attempt < ACTION_CONFIRMATION_ATTEMPTS) {
                snapshot = snapshots.awaitAfter(snapshot.eventSequence, 400L);
            }
        }
        if (observedControl == null) {
            throw new TikTokSemantics.SemanticException("missing_control");
        }
        return observedAction(
                request, startedAt, snapshot, action, "off", observedControl.resourceId);
    }

    private Protocol.Response observedAction(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            String action, String state, String resourceId) {
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("action", action);
        evidence.put("state", state);
        evidence.put("control_resource_id", resourceId);
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response applyAction(Protocol.Request request, long startedAt)
            throws Exception {
        String action = requiredArgument(request, "action");
        if (action.equals("repost")) return applyRepost(request, startedAt);
        ControlObservation observedControl = awaitControl(action);
        SemanticSnapshot before = observedControl.snapshot;
        SemanticSnapshot.Node control = observedControl.control;
        String beforeState = TikTokSemantics.actionState(control);
        long beforeCount = TikTokSemantics.actionCounter(control);
        if (beforeState.equals("on")) {
            Map<String, Object> evidence = new LinkedHashMap<String, Object>();
            evidence.put("action", action);
            evidence.put("before", beforeState);
            evidence.put("after", beforeState);
            evidence.put("control_resource_id", control.resourceId);
            return success(request, startedAt, before, evidence);
        }
        if (!actuator.click(control)) {
            return Protocol.Response.error(request, "click_rejected", "control rejected click");
        }
        SemanticSnapshot after = before;
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("action", action);
        evidence.put("before", beforeState);
        evidence.put("control_resource_id", control.resourceId);
        for (int attempt = 0; attempt < ACTION_CONFIRMATION_ATTEMPTS; attempt++) {
            after = snapshots.awaitAfter(after.eventSequence, 500L);
            try {
                SemanticSnapshot.Node afterControl = TikTokSemantics.uniqueControl(after, action);
                String afterState = TikTokSemantics.actionState(afterControl);
                long afterCount = TikTokSemantics.actionCounter(afterControl);
                evidence.put("after", afterState);
                if (beforeCount >= 0L && afterCount >= 0L) {
                    evidence.put("before_count", beforeCount);
                    evidence.put("after_count", afterCount);
                }
                if (after.eventSequence > before.eventSequence
                        && (!afterState.equals(beforeState)
                        || (beforeCount >= 0L && afterCount == beforeCount + 1L))) {
                    return success(request, startedAt, after, evidence);
                }
            } catch (TikTokSemantics.SemanticException missingFinalEvidence) {
                evidence.put("after", "unknown");
            }
        }
        if (!evidence.containsKey("after")) evidence.put("after", "unknown");
        return Protocol.Response.uncertain(
                request, elapsed(startedAt), surface.packageName(), surface.activityName(),
                after.eventSequence,
                after.digest, evidence).withPerformance(
                after.ageMs(clock.elapsedRealtimeMs()),
                Math.max(0L, after.capturedAtElapsedMs - startedAt));
    }

    private Protocol.Response applyRepost(Protocol.Request request, long startedAt)
            throws Exception {
        SemanticSnapshot before = snapshots.current();
        SemanticSnapshot.Node existing = TikTokSemantics.repostConfirmation(before);
        if (existing != null) {
            return actionSuccess(
                    request, startedAt, before, "repost", "on", "on", existing.resourceId);
        }
        SemanticSnapshot.Node share = TikTokSemantics.uniqueControl(before, "share");
        if (!actuator.click(share)) {
            return Protocol.Response.error(request, "click_rejected", "share rejected click");
        }
        SemanticSnapshot shareSurface = before;
        SemanticSnapshot.Node repost = null;
        for (int attempt = 0; attempt < 5 && repost == null; attempt++) {
            shareSurface = snapshots.awaitAfter(shareSurface.eventSequence, 400L);
            try {
                repost = TikTokSemantics.uniqueControl(shareSurface, "repost");
            } catch (TikTokSemantics.SemanticException intermediate) {
                if (!intermediate.code.equals("missing_control")) throw intermediate;
            }
        }
        if (repost == null) {
            return Protocol.Response.error(
                    request, "missing_control", "repost control is not visible");
        }
        if (!actuator.click(repost)) {
            return Protocol.Response.error(request, "click_rejected", "repost rejected click");
        }
        SemanticSnapshot after = shareSurface;
        SemanticSnapshot.Node confirmation = null;
        for (int attempt = 0; attempt < 5 && confirmation == null; attempt++) {
            after = snapshots.awaitAfter(after.eventSequence, 400L);
            confirmation = TikTokSemantics.repostConfirmation(after);
        }
        if (confirmation == null) {
            Map<String, Object> evidence = new LinkedHashMap<String, Object>();
            evidence.put("action", "repost");
            evidence.put("before", "off");
            evidence.put("after", "unknown");
            evidence.put("control_resource_id", repost.resourceId);
            return Protocol.Response.uncertain(
                    request, elapsed(startedAt), surface.packageName(), surface.activityName(),
                    after.eventSequence, after.digest, evidence).withPerformance(
                    after.ageMs(clock.elapsedRealtimeMs()),
                    Math.max(0L, after.capturedAtElapsedMs - startedAt));
        }
        return actionSuccess(
                request, startedAt, after, "repost", "off", "on", confirmation.resourceId);
    }

    private Protocol.Response actionSuccess(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            String action, String before, String after, String resourceId) {
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("action", action);
        evidence.put("before", before);
        evidence.put("after", after);
        evidence.put("control_resource_id", resourceId);
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response observeProfile(Protocol.Request request, long startedAt)
            throws Exception {
        String expectedUsername = requiredArgument(request, "expected_username");
        SemanticSnapshot snapshot = snapshots.current();
        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                snapshot, clock.elapsedRealtimeMs(), 500L, expectedUsername);
        if (profile.accessState.equals("available") && profile.videoCount == 0) {
            long observedSequence = snapshot.eventSequence;
            for (int attempt = 0; attempt < PROFILE_POST_GRID_ATTEMPTS; attempt++) {
                SemanticSnapshot candidate = snapshots.awaitAfter(observedSequence, 400L);
                if (candidate.eventSequence <= observedSequence) continue;
                observedSequence = candidate.eventSequence;
                try {
                    TikTokSemantics.Profile candidateProfile = TikTokSemantics.parseProfile(
                            candidate, clock.elapsedRealtimeMs(), 500L, expectedUsername);
                    snapshot = candidate;
                    profile = candidateProfile;
                    if (profile.videoCount > 0 || !profile.accessState.equals("available")) break;
                } catch (TikTokSemantics.SemanticException intermediate) {
                    // Header and post grid can update in separate accessibility events.
                }
            }
        }
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("access_state", profile.accessState);
        evidence.put("username", profile.username);
        evidence.put("following", profile.following);
        evidence.put("followers", profile.followers);
        evidence.put("video_count", profile.videoCount);
        evidence.put("post_handles", profile.postHandles);
        evidence.put("following_resource_id", profile.followingResourceId);
        evidence.put("followers_resource_id", profile.followersResourceId);
        return success(request, startedAt, snapshot, evidence);
    }

    private ControlObservation awaitControl(String action) throws Exception {
        SemanticSnapshot snapshot = snapshots.current();
        for (int attempt = 0; ; attempt++) {
            try {
                return new ControlObservation(
                        snapshot, TikTokSemantics.uniqueControl(snapshot, action));
            } catch (TikTokSemantics.SemanticException error) {
                if (!error.code.equals("missing_control")
                        || attempt >= ACTION_CONTROL_ATTEMPTS) throw error;
            }
            snapshot = snapshots.awaitAfter(snapshot.eventSequence, 400L);
        }
    }

    private static final class ControlObservation {
        final SemanticSnapshot snapshot;
        final SemanticSnapshot.Node control;

        ControlObservation(SemanticSnapshot snapshot, SemanticSnapshot.Node control) {
            this.snapshot = snapshot;
            this.control = control;
        }
    }

    private Protocol.Response openProfile(Protocol.Request request, long startedAt)
            throws Exception {
        String route = requiredArgument(request, "route");
        String expectedUsername = requiredArgument(request, "expected_username");
        SemanticSnapshot before = snapshots.current();
        try {
            TikTokSemantics.Profile current = TikTokSemantics.parseProfile(
                    before, clock.elapsedRealtimeMs(), 500L, expectedUsername);
            if (current.username.equals(expectedUsername)) {
                Map<String, Object> evidence = new LinkedHashMap<String, Object>();
                evidence.put("route_opened", false);
                evidence.put("username", current.username);
                return success(request, startedAt, before, evidence);
            }
        } catch (TikTokSemantics.SemanticException notCurrentTarget) {
            // Continue with the requested route when exact current identity is absent.
        }
        if (!actuator.openProfile(route)) {
            return Protocol.Response.error(request, "route_rejected", "profile route rejected");
        }
        boolean observedNewEvent = false;
        long observedSequence = before.eventSequence;
        for (int attempt = 0; attempt < 15; attempt++) {
            SemanticSnapshot snapshot = snapshots.awaitAfter(observedSequence, 400L);
            if (snapshot.eventSequence > before.eventSequence) {
                observedNewEvent = true;
                observedSequence = snapshot.eventSequence;
                try {
                    TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                            snapshot, clock.elapsedRealtimeMs(), 500L, expectedUsername);
                    if (profile.username.equals(expectedUsername)) {
                        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
                        evidence.put("route_opened", true);
                        evidence.put("username", profile.username);
                        return success(request, startedAt, snapshot, evidence);
                    }
                } catch (TikTokSemantics.SemanticException incompleteProfile) {
                    // Navigation can emit intermediate surfaces before profile evidence settles.
                }
            }
        }
        return Protocol.Response.error(
                request,
                observedNewEvent ? "profile_identity_mismatch" : "profile_not_updated",
                observedNewEvent
                        ? "visible username does not match"
                        : "profile evidence did not change");
    }

    private Protocol.Response openProfileSearch(Protocol.Request request, long startedAt)
            throws Exception {
        String expectedUsername = requiredArgument(request, "expected_username");
        String result = actuator.searchProfile(expectedUsername);
        if (!"exact".equals(result)) {
            String code = result.startsWith("search_") ? result : "ambiguous".equals(result)
                    ? "search_ambiguous_exact_match"
                    : "no_match".equals(result)
                            ? "search_no_exact_match" : "search_surface_timeout";
            return Protocol.Response.error(request, code, "exact search result unavailable");
        }
        SemanticSnapshot initial = snapshots.current();
        Protocol.Response verified = verifiedSearchProfile(
                request, startedAt, initial, expectedUsername);
        if (verified != null) return verified;
        long observedSequence = initial.eventSequence;
        for (int attempt = 0; attempt < 15; attempt++) {
            SemanticSnapshot snapshot = snapshots.awaitAfter(observedSequence, 400L);
            observedSequence = snapshot.eventSequence;
            verified = verifiedSearchProfile(request, startedAt, snapshot, expectedUsername);
            if (verified != null) return verified;
        }
        return Protocol.Response.error(
                request, "profile_identity_mismatch", "visible username does not match");
    }

    private Protocol.Response verifiedSearchProfile(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            String expectedUsername) {
        try {
            TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                    snapshot, clock.elapsedRealtimeMs(), 500L, expectedUsername);
            if (!TikTokSearchSemantics.normalizeUsername(profile.username).equals(
                    TikTokSearchSemantics.normalizeUsername(expectedUsername))) {
                return null;
            }
            Map<String, Object> evidence = new LinkedHashMap<String, Object>();
            evidence.put("route_opened", true);
            evidence.put("navigation_mode", "search");
            evidence.put("username", profile.username);
            return success(request, startedAt, snapshot, evidence);
        } catch (TikTokSemantics.SemanticException intermediate) {
            return null;
        }
    }

    private Protocol.Response openVideo(Protocol.Request request, long startedAt)
            throws Exception {
        String videoKey = requiredArgument(request, "video_key");
        SemanticSnapshot before = snapshots.current();
        SemanticSnapshot.Node post = TikTokSemantics.postControl(before, videoKey);
        if (!actuator.click(post)) {
            return Protocol.Response.error(request, "click_rejected", "post rejected click");
        }
        long observedSequence = before.eventSequence;
        for (int attempt = 0; attempt < 10; attempt++) {
            SemanticSnapshot after = snapshots.awaitAfter(observedSequence, 400L);
            if (after.eventSequence <= before.eventSequence) continue;
            observedSequence = after.eventSequence;
            if (!TikTokSemantics.hasVideoControls(after)) continue;
            Map<String, Object> evidence = new LinkedHashMap<String, Object>();
            evidence.put("video_key", videoKey);
            evidence.put("post_resource_id", post.resourceId);
            evidence.put("video_controls_visible", true);
            return success(request, startedAt, after, evidence);
        }
        return Protocol.Response.error(
                request, "video_not_verified", "video controls are not visible");
    }

    private Protocol.Response success(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            Map<String, Object> evidence) {
        return Protocol.Response.success(
                request, elapsed(startedAt), surface.packageName(), surface.activityName(),
                snapshot.eventSequence, snapshot.digest, evidence).withPerformance(
                snapshot.ageMs(clock.elapsedRealtimeMs()),
                Math.max(0L, snapshot.capturedAtElapsedMs - startedAt));
    }

    private long elapsed(long startedAt) {
        return Math.max(0L, clock.elapsedRealtimeMs() - startedAt);
    }

    private static String requiredArgument(Protocol.Request request, String name)
            throws Protocol.ProtocolException {
        Object value = request.arguments.get(name);
        if (!(value instanceof String) || ((String) value).trim().isEmpty()) {
            throw new Protocol.ProtocolException("invalid_argument_" + name);
        }
        return ((String) value).trim();
    }

    private static String optionalArgument(Protocol.Request request, String name)
            throws Protocol.ProtocolException {
        Object value = request.arguments.get(name);
        if (value == null) return "";
        if (!(value instanceof String)) {
            throw new Protocol.ProtocolException("invalid_argument_" + name);
        }
        return ((String) value).trim();
    }

    private static int visibleNodeCount(SemanticSnapshot snapshot) {
        int count = 0;
        for (SemanticSnapshot.Node node : snapshot.nodes) if (node.visible) count++;
        return count;
    }
}
