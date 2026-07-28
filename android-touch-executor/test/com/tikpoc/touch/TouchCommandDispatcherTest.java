package com.tikpoc.touch;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

public final class TouchCommandDispatcherTest {
    public static void main(String[] args) throws Exception {
        healthIsReadOnly();
        healthReadsCurrentSurface();
        homeBrowseIsBoundedAndReadOnly();
        interruptionObservationIsReadOnly();
        verificationResetsWithTwoAndroidBackActions();
        verificationResetRequiresChallengeToDisappear();
        ordinaryDialogRecoversOnceToHome();
        failedHomeRecoveryIsBounded();
        opensCanonicalCommentVideoAndVerifiesIdentity();
        submitsFirstLevelCommentOnceAndVerifiesVisibleText();
        submittedCommentObservationIsReadOnly();
        opensOnePostAndVerifiesVideoControls();
        waitsForVideoEventBeforeVerifyingControls();
        returnsBoundedErrorWhenVideoHandleIsMissing();
        returnsBoundedErrorWhenActuatorFails();
        opensProfileOnlyAfterExactNewIdentity();
        alreadyOpenExactProfileDoesNotRequireANavigationEvent();
        waitsForProfileEventBeforeVerifyingIdentity();
        waitsThroughSlowProfileIntermediateEvents();
        stopsProfileVerificationAfterFifteenChecks();
        searchRequiresExactProfileEvidence();
        searchAcceptsAnAlreadyLoadedExactProfile();
        searchNoMatchIsTerminalEvidence();
        actionClicksOnceAndVerifiesResult();
        actionWaitsPastAStalePostClickSnapshot();
        actionWaitsForDelayedPlatformStatePropagation();
        actionConfirmsFromVisibleCounterIncrement();
        alreadySelectedActionIsConfirmedWithoutClick();
        repostUsesShareSurfaceAndVerifiesResult();
        missingFinalEvidenceIsUncertainWithoutSecondClick();
        diagnosticsContainsNoVisibleText();
        System.out.println("TouchCommandDispatcherTest PASS");
    }

    private static void homeBrowseIsBoundedAndReadOnly() throws Exception {
        Fixture fixture = new Fixture();
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("browse_home", empty()));
        check(response.values.get("status").equals("ok"), "home browse status");
        check(fixture.actuator.homeBrowses == 1, "one bounded home browse");
        @SuppressWarnings("unchecked")
        Map<String, Object> evidence = (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("home_visible").equals(true), "home visible evidence");
        check(evidence.get("browse_performed").equals(true), "browse performed evidence");
    }

    private static void interruptionObservationIsReadOnly() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(
                interruptionSnapshot("请完成下列验证后继续", 1));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("observe_interruption", empty()));

        check(response.values.get("status").equals("ok"), "interruption observed");
        @SuppressWarnings("unchecked") Map<String, Object> evidence =
                (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("interruption").equals("verification_required"),
                "verification classified");
        check(fixture.actuator.dismissals == 0, "observation does not dismiss");
        check(fixture.actuator.homeRecoveries == 0, "observation does not navigate");
    }

    private static void verificationResetsWithTwoAndroidBackActions() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                interruptionSnapshot("Verify to continue", 1), homeSnapshot(2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("recover_home", empty()));

        check(response.values.get("status").equals("ok"), "verification reset recovery");
        check(fixture.actuator.verificationBacks == 2, "two Android back actions");
        @SuppressWarnings("unchecked") Map<String, Object> evidence =
                (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("verification_reset_count").equals(2L),
                "verification reset evidence");
    }

    private static void verificationResetRequiresChallengeToDisappear() throws Exception {
        Fixture fixture = new Fixture();
        SemanticSnapshot blockedHome = controlsSnapshot(
                2, label("interruption", "Verify to continue"),
                label("home", "Home"), label("feed", "For You"));
        fixture.source.snapshots = Arrays.asList(
                interruptionSnapshot("Verify to continue", 1), blockedHome);

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("recover_home", empty()));

        check(response.values.get("status").equals("error"),
                "visible challenge blocks false home success");
        @SuppressWarnings("unchecked") Map<String, Object> error =
                (Map<String, Object>) response.values.get("error");
        check(error.get("code").equals("verification_required"),
                "remaining challenge preserved");
        check(fixture.actuator.verificationBacks == 2,
                "remaining challenge does not create reset loop");
    }

    private static void ordinaryDialogRecoversOnceToHome() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                interruptionSnapshot("与好友一起使用 TikTok 会更有趣", 1),
                homeSnapshot(2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("recover_home", empty()));

        check(response.values.get("status").equals("ok"), "home recovery verified");
        check(fixture.actuator.dismissals == 1, "ordinary dialog dismissed once");
        check(fixture.actuator.homeRecoveries == 1, "home activated once");
        @SuppressWarnings("unchecked") Map<String, Object> evidence =
                (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("home_visible").equals(true), "home evidence visible");
    }

    private static void failedHomeRecoveryIsBounded() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(actionSnapshot(false, 1));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("recover_home", empty()));

        check(response.values.get("status").equals("error"), "failed recovery recorded");
        @SuppressWarnings("unchecked") Map<String, Object> error =
                (Map<String, Object>) response.values.get("error");
        check(error.get("code").equals("home_recovery_failed"), "bounded recovery error");
        check(fixture.actuator.dismissals == 0, "no ordinary interruption dismissal");
        check(fixture.actuator.homeRecoveries == 1, "home activated only once");
        check(fixture.source.index == 6, "home verification capped at five checks");
    }

    private static void opensCanonicalCommentVideoAndVerifiesIdentity() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                actionSnapshot(false, 1), commentVideoSnapshot(2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_comment_video", map("video_id", "7523456789012345678")));

        check(response.values.get("status").equals("ok"), "comment video verified");
        check(fixture.actuator.commentVideoOpens == 1, "comment video opened once");
    }

    private static void submitsFirstLevelCommentOnceAndVerifiesVisibleText()
            throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                commentVideoSnapshot(1), submittedCommentSnapshot(2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("submit_first_level_comment", map("publish_text", "Original comment")));

        check(response.values.get("status").equals("ok"), "comment visibly confirmed");
        check(fixture.actuator.commentSubmits == 1, "comment submitted exactly once");
    }

    private static void submittedCommentObservationIsReadOnly() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(submittedCommentSnapshot(1));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("observe_submitted_comment", map("publish_text", "Original comment")));

        check(response.values.get("status").equals("ok"), "submitted text observed");
        check(fixture.actuator.commentSubmits == 0, "observation does not resubmit");
    }

    private static void searchRequiresExactProfileEvidence() throws Exception {
        Fixture fixture = new Fixture();
        fixture.actuator.searchResult = "exact";
        fixture.source.snapshots = Arrays.asList(actionSnapshot(false, 1), profileSnapshot(2));
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile_search", map("expected_username", "target_user")));
        check(response.values.get("status").equals("ok"), "search profile verified");
    }

    private static void searchNoMatchIsTerminalEvidence() throws Exception {
        Fixture fixture = new Fixture();
        fixture.actuator.searchResult = "no_match";
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile_search", map("expected_username", "target_user")));
        @SuppressWarnings("unchecked") Map<String, Object> error =
                (Map<String, Object>) response.values.get("error");
        check(error.get("code").equals("search_no_exact_match"), "no exact match code");
    }

    private static void searchAcceptsAnAlreadyLoadedExactProfile() throws Exception {
        Fixture fixture = new Fixture();
        fixture.actuator.searchResult = "exact";
        fixture.source.snapshots = Collections.singletonList(profileSnapshot(2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile_search", map("expected_username", "target_user")));

        check(response.values.get("status").equals("ok"), "loaded search profile verified");
        check(fixture.source.index == 1, "loaded profile accepted without waiting for new event");
    }

    private static void opensProfileOnlyAfterExactNewIdentity() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(actionSnapshot(false, 1), profileSnapshot());
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile", map("route", "https://www.tiktok.com/@target_user")));
        check(response.values.get("status").equals("ok"), "profile verified");
        @SuppressWarnings("unchecked")
        Map<String, Object> evidence = (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("username").equals("target_user"), "profile identity");
    }

    private static void alreadyOpenExactProfileDoesNotRequireANavigationEvent()
            throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(profileSnapshot());

        Map<String, Object> arguments = map(
                "route", "https://www.tiktok.com/@target_user");
        arguments.put("expected_username", "target_user");
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile", arguments));

        check(response.values.get("status").equals("ok"), "current profile verified");
        check(fixture.actuator.profileOpens == 0, "current profile not reopened");
    }

    private static void waitsForProfileEventBeforeVerifyingIdentity() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                actionSnapshot(false, 1), actionSnapshot(false, 1),
                actionSnapshot(false, 1), profileSnapshot());
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile", map("route", "https://www.tiktok.com/@target_user")));
        check(response.values.get("status").equals("ok"), "delayed profile verified");
        check(fixture.source.index == 4, "profile snapshots polled");
    }

    private static void waitsThroughSlowProfileIntermediateEvents() throws Exception {
        Fixture fixture = new Fixture();
        java.util.List<SemanticSnapshot> snapshots = new java.util.ArrayList<>();
        snapshots.add(actionSnapshot(false, 1));
        for (int sequence = 2; sequence < 14; sequence++) {
            snapshots.add(actionSnapshot(false, sequence));
        }
        snapshots.add(profileSnapshot(14));
        fixture.source.snapshots = snapshots;

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile", map("route", "https://www.tiktok.com/@target_user")));

        check(response.values.get("status").equals("ok"), "slow profile verified");
        check(fixture.source.index == 14, "slow profile snapshots polled");
    }

    private static void stopsProfileVerificationAfterFifteenChecks() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(actionSnapshot(false, 1));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_profile", map("route", "https://www.tiktok.com/@target_user")));

        check(response.values.get("status").equals("error"), "profile timeout status");
        check(fixture.source.index == 16, "profile verification is bounded to fifteen checks");
    }

    private static void healthReadsCurrentSurface() throws Exception {
        Fixture fixture = new Fixture();
        fixture.surface.activityName = "CurrentActivity";
        Protocol.Response response = fixture.dispatcher.dispatch(request("health", empty()));
        check(response.values.get("activity_name").equals("CurrentActivity"), "current activity");
    }

    private static void opensOnePostAndVerifiesVideoControls() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(profileSnapshot(), actionSnapshot(false, 3));
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_video", map("video_key", "post:0")));
        check(fixture.actuator.clicks == 1, "one post click");
        check(response.values.get("status").equals("ok"), "video verified");
    }

    private static void waitsForVideoEventBeforeVerifyingControls() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                profileSnapshot(), profileSnapshot(), profileSnapshot(),
                actionSnapshot(false, 3));
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_video", map("video_key", "post:0")));
        check(fixture.actuator.clicks == 1, "delayed video clicked once");
        check(response.values.get("status").equals("ok"), "delayed video verified");
        check(fixture.source.index == 4, "video snapshots polled");
    }

    private static void returnsBoundedErrorWhenVideoHandleIsMissing() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(actionSnapshot(false, 1));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_video", map("video_key", "post:0")));

        check(response.values.get("status").equals("error"), "missing video error status");
        @SuppressWarnings("unchecked")
        Map<String, Object> error = (Map<String, Object>) response.values.get("error");
        check(error.get("code").equals("missing_post_handle"), "missing video error code");
    }

    private static void returnsBoundedErrorWhenActuatorFails() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(profileSnapshot());
        fixture.actuator.failure = new IllegalStateException("synthetic actuator failure");

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("open_video", map("video_key", "post:0")));

        check(response.values.get("status").equals("error"), "actuator error status");
        @SuppressWarnings("unchecked")
        Map<String, Object> error = (Map<String, Object>) response.values.get("error");
        check(error.get("code").equals("command_failed"), "actuator error code");
    }

    private static void healthIsReadOnly() throws Exception {
        Fixture fixture = new Fixture();
        Protocol.Response response = fixture.dispatcher.dispatch(request("health", empty()));
        check(response.values.get("status").equals("ok"), "health status");
        check(fixture.actuator.clicks == 0, "health read only");
    }

    private static void actionClicksOnceAndVerifiesResult() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(actionSnapshot(false, 1), actionSnapshot(true, 2));
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "like")));
        check(fixture.actuator.clicks == 1, "one click");
        @SuppressWarnings("unchecked")
        Map<String, Object> evidence = (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("before").equals("off"), "before");
        check(evidence.get("after").equals("on"), "after");
        check(evidence.get("control_resource_id").equals("like_button"), "control");
    }

    private static void actionWaitsPastAStalePostClickSnapshot() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                actionSnapshot(false, 1),
                actionSnapshot(false, 1),
                actionSnapshot(true, 2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "like")));

        check(response.values.get("status").equals("ok"), "delayed action verified");
        check(fixture.actuator.clicks == 1, "delayed action clicked once");
        check(fixture.source.index == 3, "stale action snapshot polled once");
    }

    private static void actionConfirmsFromVisibleCounterIncrement() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                countedActionSnapshot(2, 1), countedActionSnapshot(3, 2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "favorite")));

        check(response.values.get("status").equals("ok"), "counter action verified");
        check(fixture.actuator.clicks == 1, "counter action clicked once");
        @SuppressWarnings("unchecked")
        Map<String, Object> evidence = (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("before_count").equals(2L), "counter before evidence");
        check(evidence.get("after_count").equals(3L), "counter after evidence");
    }

    private static void actionWaitsForDelayedPlatformStatePropagation() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                actionSnapshot(false, 1), actionSnapshot(false, 1),
                actionSnapshot(false, 1), actionSnapshot(false, 1),
                actionSnapshot(false, 1), actionSnapshot(false, 1),
                actionSnapshot(true, 2));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "like")));

        check(response.values.get("status").equals("uncertain"),
                "late platform state remains outside bounded observation");
        check(fixture.actuator.clicks == 1, "delayed platform state clicked once");
        check(fixture.source.index == 4, "late platform state stops after three polls");
    }

    private static void alreadySelectedActionIsConfirmedWithoutClick() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(actionSnapshot(true, 1));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "like")));

        check(response.values.get("status").equals("ok"), "selected action confirmed");
        check(fixture.actuator.clicks == 0, "selected action not clicked");
    }

    private static void repostUsesShareSurfaceAndVerifiesResult() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Arrays.asList(
                controlsSnapshot(1, control("share", "分享视频。4 次分享")),
                controlsSnapshot(2, label("loading", "正在加载")),
                controlsSnapshot(3, control("repost", "转发")),
                controlsSnapshot(4, label("loading", "正在处理")),
                controlsSnapshot(5, label("repost_state", "你已转发")));

        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "repost")));

        check(response.values.get("status").equals("ok"), "repost verified");
        check(fixture.actuator.clicks == 2, "share and repost clicked once each");
        @SuppressWarnings("unchecked")
        Map<String, Object> evidence = (Map<String, Object>) response.values.get("evidence");
        check(evidence.get("control_resource_id").equals("repost_state"),
                "repost confirmation evidence source");
        check(fixture.source.index == 5, "repost intermediate snapshots polled");
    }

    private static void missingFinalEvidenceIsUncertainWithoutSecondClick() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(actionSnapshot(false, 1));
        Protocol.Response response = fixture.dispatcher.dispatch(
                request("apply_action", map("action", "like")));
        check(fixture.actuator.clicks == 1, "no second click");
        check(response.values.get("status").equals("uncertain"), "uncertain status");
        check(fixture.source.index == 4, "action observation capped at three polls");
    }

    private static void diagnosticsContainsNoVisibleText() throws Exception {
        Fixture fixture = new Fixture();
        fixture.source.snapshots = Collections.singletonList(actionSnapshot(false, 1));
        String encoded = Protocol.encodeResponse(
                fixture.dispatcher.dispatch(request("diagnostics", empty())));
        check(!encoded.contains("secret visible text"), "redacted text");
        check(encoded.contains("node_count"), "bounded metadata");
    }

    private static final class Fixture {
        final FakeSource source = new FakeSource();
        final FakeActuator actuator = new FakeActuator();
        final FakeSurface surface = new FakeSurface();
        final TouchCommandDispatcher dispatcher = new TouchCommandDispatcher(
                source, actuator, () -> 1_000L, surface);
    }

    private static final class FakeSurface implements TouchCommandDispatcher.SurfaceSource {
        String activityName = "MainActivity";

        @Override
        public String packageName() { return "com.zhiliaoapp.musically"; }

        @Override
        public String activityName() { return activityName; }
    }

    private static final class FakeSource implements TouchCommandDispatcher.SnapshotSource {
        java.util.List<SemanticSnapshot> snapshots =
                Collections.singletonList(actionSnapshot(false, 1));
        int index;

        @Override
        public SemanticSnapshot current() {
            return snapshots.get(Math.min(index++, snapshots.size() - 1));
        }
    }

    private static final class FakeActuator implements TouchCommandDispatcher.Actuator {
        int clicks;
        int profileOpens;
        int homeBrowses;
        int dismissals;
        int homeRecoveries;
        int verificationBacks;
        int commentVideoOpens;
        int commentSubmits;
        RuntimeException failure;
        String searchResult = "timeout";

        @Override
        public boolean click(SemanticSnapshot.Node node) {
            if (failure != null) throw failure;
            clicks++;
            return true;
        }

        @Override
        public boolean openProfile(String route) {
            profileOpens++;
            return true;
        }

        @Override
        public String searchProfile(String username) { return searchResult; }

        @Override
        public boolean browseHomeReadOnly() {
            homeBrowses++;
            return true;
        }

        @Override
        public boolean dismissOrdinaryInterruption(String kind) {
            dismissals++;
            return true;
        }

        @Override
        public boolean resetVerification() {
            verificationBacks += 2;
            return true;
        }

        @Override
        public boolean returnToHome() {
            homeRecoveries++;
            return true;
        }

        @Override
        public boolean openCommentVideo(String videoId, String videoUrl) {
            commentVideoOpens++;
            return true;
        }

        @Override
        public boolean submitFirstLevelComment(String text) {
            commentSubmits++;
            return true;
        }
    }

    private static SemanticSnapshot actionSnapshot(boolean selected, long sequence) {
        try {
            SemanticSnapshot.Node control = new SemanticSnapshot.Node(
                    "like_button", "Button", "secret visible text", "Like",
                    new SemanticSnapshot.Bounds(0, 0, 100, 50), true, true, true, selected,
                    Collections.<SemanticSnapshot.Node>emptyList());
            SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                    "root", "Frame", "", "", new SemanticSnapshot.Bounds(0, 0, 1080, 1920),
                    true, false, true, false, Collections.singletonList(control));
            return SemanticSnapshot.fromRoot(root, sequence, 1_000L);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private static SemanticSnapshot countedActionSnapshot(long count, long sequence) {
        try {
            SemanticSnapshot.Node counter = new SemanticSnapshot.Node(
                    "favorite_count", "TextView", Long.toString(count), "",
                    new SemanticSnapshot.Bounds(0, 40, 100, 50), true, false, true, false,
                    Collections.<SemanticSnapshot.Node>emptyList());
            SemanticSnapshot.Node control = new SemanticSnapshot.Node(
                    "favorite_button", "Button", "", "收藏",
                    new SemanticSnapshot.Bounds(0, 0, 100, 50), true, true, true, false,
                    Collections.singletonList(counter));
            SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                    "root", "Frame", "", "", new SemanticSnapshot.Bounds(0, 0, 1080, 1920),
                    true, false, true, false, Collections.singletonList(control));
            return SemanticSnapshot.fromRoot(root, sequence, 1_000L);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private static SemanticSnapshot controlsSnapshot(
            long sequence, SemanticSnapshot.Node... controls) {
        try {
            SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                    "root", "Frame", "", "", new SemanticSnapshot.Bounds(0, 0, 1080, 1920),
                    true, false, true, false, Arrays.asList(controls));
            return SemanticSnapshot.fromRoot(root, sequence, 1_000L);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private static SemanticSnapshot.Node control(String resourceId, String description) {
        return new SemanticSnapshot.Node(
                resourceId, "Button", "", description,
                new SemanticSnapshot.Bounds(0, 0, 100, 50), true, true, true, false,
                Collections.<SemanticSnapshot.Node>emptyList());
    }

    private static SemanticSnapshot.Node label(String resourceId, String text) {
        return new SemanticSnapshot.Node(
                resourceId, "TextView", text, "",
                new SemanticSnapshot.Bounds(0, 0, 100, 50), true, false, true, false,
                Collections.<SemanticSnapshot.Node>emptyList());
    }

    private static SemanticSnapshot interruptionSnapshot(String text, long sequence) {
        return controlsSnapshot(sequence, label("interruption", text));
    }

    private static SemanticSnapshot homeSnapshot(long sequence) {
        return controlsSnapshot(sequence, label("home", "首页"), label("feed", "推荐"));
    }

    private static SemanticSnapshot commentVideoSnapshot(long sequence) {
        return controlsSnapshot(
                sequence,
                label("video_id", "7523456789012345678"),
                control("like_button", "Like"));
    }

    private static SemanticSnapshot submittedCommentSnapshot(long sequence) {
        return controlsSnapshot(sequence, label("comment_text", "Original comment"));
    }

    private static SemanticSnapshot profileSnapshot() {
        return profileSnapshot(2L);
    }

    private static SemanticSnapshot profileSnapshot(long sequence) {
        try {
            SemanticSnapshot.Node post = new SemanticSnapshot.Node(
                    "video_item", "ImageView", "", "Video", new SemanticSnapshot.Bounds(
                    0, 100, 100, 200), true, true, true, false,
                    Collections.<SemanticSnapshot.Node>emptyList());
            SemanticSnapshot.Node username = new SemanticSnapshot.Node(
                    "username", "TextView", "@target_user", "",
                    new SemanticSnapshot.Bounds(0, 0, 100, 50), true, false, true, false,
                    Collections.<SemanticSnapshot.Node>emptyList());
            SemanticSnapshot.Node following = new SemanticSnapshot.Node(
                    "following_count", "TextView", "12", "Following",
                    new SemanticSnapshot.Bounds(0, 50, 100, 100), true, false, true, false,
                    Collections.<SemanticSnapshot.Node>emptyList());
            SemanticSnapshot.Node followers = new SemanticSnapshot.Node(
                    "followers_count", "TextView", "4", "Followers",
                    new SemanticSnapshot.Bounds(100, 50, 200, 100), true, false, true, false,
                    Collections.<SemanticSnapshot.Node>emptyList());
            SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                    "root", "Frame", "", "", new SemanticSnapshot.Bounds(0, 0, 1080, 1920),
                    true, false, true, false,
                    Arrays.asList(username, following, followers, post));
            return SemanticSnapshot.fromRoot(root, sequence, 1_000L);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private static Protocol.Request request(String command, Map<String, Object> arguments)
            throws Exception {
        String argumentsJson = "{}";
        if (arguments.containsKey("action")) {
            argumentsJson = "{\"action\":\"" + arguments.get("action") + "\"}";
        }
        if (arguments.containsKey("video_key")) argumentsJson = "{\"video_key\":\"post:0\"}";
        if (arguments.containsKey("route")) {
            argumentsJson = "{\"route\":\"https://www.tiktok.com/@target_user\","
                    + "\"expected_username\":\"target_user\"}";
        }
        if (arguments.containsKey("expected_username")
                && !arguments.containsKey("route")) {
            argumentsJson = "{\"expected_username\":\"target_user\"}";
        }
        if (arguments.containsKey("video_id")) {
            argumentsJson = "{\"video_id\":\"7523456789012345678\","
                    + "\"video_url\":\"https://www.tiktok.com/@bag/video/"
                    + "7523456789012345678\"}";
        }
        if (arguments.containsKey("publish_text")) {
            argumentsJson = "{\"publish_text\":\"Original comment\"}";
        }
        return Protocol.parseRequest(
                "{\"version\":1,\"command_id\":\"cmd-" + command + "\","
                + "\"command\":\"" + command + "\",\"device_id\":\"device-1\","
                + "\"account_id\":\"account-1\",\"fence_token\":7,"
                + "\"assignment_id\":19,\"phase\":\"profile_opening\","
                + "\"deadline_elapsed_ms\":9000,\"arguments\":" + argumentsJson + "}", 0L);
    }

    private static Map<String, Object> empty() {
        return Collections.emptyMap();
    }

    private static Map<String, Object> map(String key, Object value) {
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put(key, value);
        return result;
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
