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
        opensOnePostAndVerifiesVideoControls();
        waitsForVideoEventBeforeVerifyingControls();
        returnsBoundedErrorWhenVideoHandleIsMissing();
        returnsBoundedErrorWhenActuatorFails();
        opensProfileOnlyAfterExactNewIdentity();
        alreadyOpenExactProfileDoesNotRequireANavigationEvent();
        waitsForProfileEventBeforeVerifyingIdentity();
        waitsThroughSlowProfileIntermediateEvents();
        searchRequiresExactProfileEvidence();
        searchAcceptsAnAlreadyLoadedExactProfile();
        searchNoMatchIsTerminalEvidence();
        actionClicksOnceAndVerifiesResult();
        actionWaitsPastAStalePostClickSnapshot();
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
