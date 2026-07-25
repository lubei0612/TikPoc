package com.tikpoc.touch;

import java.util.LinkedHashMap;
import java.util.Map;

public final class TouchCommandDispatcher {
    public interface SnapshotSource {
        SemanticSnapshot current() throws Exception;
    }

    public interface Actuator {
        boolean click(SemanticSnapshot.Node node) throws Exception;
        boolean openProfile(String route) throws Exception;
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
        long startedAt = clock.elapsedRealtimeMs();
        if (request.command.equals("health")) return health(request, startedAt);
        if (request.command.equals("diagnostics")) return diagnostics(request, startedAt);
        if (request.command.equals("apply_action")) return applyAction(request, startedAt);
        if (request.command.equals("observe_action")) return observeAction(request, startedAt);
        if (request.command.equals("observe_profile")) return observeProfile(request, startedAt);
        if (request.command.equals("open_profile")) return openProfile(request, startedAt);
        if (request.command.equals("open_video")) return openVideo(request, startedAt);
        return Protocol.Response.error(request, "unsupported_command", "command unavailable");
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
        SemanticSnapshot.Node control = TikTokSemantics.uniqueControl(snapshot, action);
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("action", action);
        evidence.put("state", TikTokSemantics.actionState(control));
        evidence.put("control_resource_id", control.resourceId);
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response applyAction(Protocol.Request request, long startedAt)
            throws Exception {
        String action = requiredArgument(request, "action");
        SemanticSnapshot before = snapshots.current();
        SemanticSnapshot.Node control = TikTokSemantics.uniqueControl(before, action);
        String beforeState = TikTokSemantics.actionState(control);
        if (!actuator.click(control)) {
            return Protocol.Response.error(request, "click_rejected", "control rejected click");
        }
        SemanticSnapshot after = snapshots.current();
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("action", action);
        evidence.put("before", beforeState);
        evidence.put("control_resource_id", control.resourceId);
        try {
            String afterState = TikTokSemantics.actionState(
                    TikTokSemantics.uniqueControl(after, action));
            evidence.put("after", afterState);
            if (after.eventSequence > before.eventSequence && !afterState.equals(beforeState)) {
                return success(request, startedAt, after, evidence);
            }
        } catch (TikTokSemantics.SemanticException missingFinalEvidence) {
            evidence.put("after", "unknown");
        }
        return Protocol.Response.uncertain(
                request, elapsed(startedAt), surface.packageName(), surface.activityName(),
                after.eventSequence,
                after.digest, evidence);
    }

    private Protocol.Response observeProfile(Protocol.Request request, long startedAt)
            throws Exception {
        SemanticSnapshot snapshot = snapshots.current();
        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                snapshot, clock.elapsedRealtimeMs(), 500L);
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

    private Protocol.Response openProfile(Protocol.Request request, long startedAt)
            throws Exception {
        String route = requiredArgument(request, "route");
        String expectedUsername = requiredArgument(request, "expected_username");
        SemanticSnapshot before = snapshots.current();
        if (!actuator.openProfile(route)) {
            return Protocol.Response.error(request, "route_rejected", "profile route rejected");
        }
        SemanticSnapshot snapshot = snapshots.current();
        if (snapshot.eventSequence <= before.eventSequence) {
            return Protocol.Response.error(
                    request, "profile_not_updated", "profile evidence did not change");
        }
        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                snapshot, clock.elapsedRealtimeMs(), 500L);
        if (!profile.username.equals(expectedUsername)) {
            return Protocol.Response.error(
                    request, "profile_identity_mismatch", "visible username does not match");
        }
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("route_opened", true);
        evidence.put("username", profile.username);
        return success(request, startedAt, snapshot, evidence);
    }

    private Protocol.Response openVideo(Protocol.Request request, long startedAt)
            throws Exception {
        String videoKey = requiredArgument(request, "video_key");
        SemanticSnapshot before = snapshots.current();
        SemanticSnapshot.Node post = TikTokSemantics.postControl(before, videoKey);
        if (!actuator.click(post)) {
            return Protocol.Response.error(request, "click_rejected", "post rejected click");
        }
        SemanticSnapshot after = snapshots.current();
        if (after.eventSequence <= before.eventSequence
                || !TikTokSemantics.hasVideoControls(after)) {
            return Protocol.Response.error(
                    request, "video_not_verified", "video controls are not visible");
        }
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("video_key", videoKey);
        evidence.put("post_resource_id", post.resourceId);
        evidence.put("video_controls_visible", true);
        return success(request, startedAt, after, evidence);
    }

    private Protocol.Response success(
            Protocol.Request request, long startedAt, SemanticSnapshot snapshot,
            Map<String, Object> evidence) {
        return Protocol.Response.success(
                request, elapsed(startedAt), surface.packageName(), surface.activityName(),
                snapshot.eventSequence, snapshot.digest, evidence);
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

    private static int visibleNodeCount(SemanticSnapshot snapshot) {
        int count = 0;
        for (SemanticSnapshot.Node node : snapshot.nodes) if (node.visible) count++;
        return count;
    }
}
