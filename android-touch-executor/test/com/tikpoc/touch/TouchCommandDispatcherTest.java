package com.tikpoc.touch;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

public final class TouchCommandDispatcherTest {
    public static void main(String[] args) throws Exception {
        healthIsReadOnly();
        healthReadsCurrentSurface();
        opensOnePostAndVerifiesVideoControls();
        opensProfileOnlyAfterExactNewIdentity();
        actionClicksOnceAndVerifiesResult();
        missingFinalEvidenceIsUncertainWithoutSecondClick();
        diagnosticsContainsNoVisibleText();
        System.out.println("TouchCommandDispatcherTest PASS");
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

        @Override
        public boolean click(SemanticSnapshot.Node node) {
            clicks++;
            return true;
        }

        @Override
        public boolean openProfile(String route) {
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

    private static SemanticSnapshot profileSnapshot() {
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
            return SemanticSnapshot.fromRoot(root, 2L, 1_000L);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private static Protocol.Request request(String command, Map<String, Object> arguments)
            throws Exception {
        String argumentsJson = "{}";
        if (arguments.containsKey("action")) argumentsJson = "{\"action\":\"like\"}";
        if (arguments.containsKey("video_key")) argumentsJson = "{\"video_key\":\"post:0\"}";
        if (arguments.containsKey("route")) {
            argumentsJson = "{\"route\":\"https://www.tiktok.com/@target_user\","
                    + "\"expected_username\":\"target_user\"}";
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
