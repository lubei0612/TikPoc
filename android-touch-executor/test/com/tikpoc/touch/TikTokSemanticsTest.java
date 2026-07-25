package com.tikpoc.touch;

import java.util.Arrays;
import java.util.Collections;

public final class TikTokSemanticsTest {
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    public static void main(String[] args) throws Exception {
        parsesCoherentProfileEvidence();
        parsesAbbreviatedCounts();
        classifiesPrivateAndUnavailableProfiles();
        rejectsIncompleteAndStaleEvidence();
        selectsOnlyOneVisibleControl();
        System.out.println("TikTokSemanticsTest PASS");
    }

    private static void parsesCoherentProfileEvidence() throws Exception {
        SemanticSnapshot snapshot = snapshot(
                node("username", "  @target_user  ", "", false),
                node("following_count", "120", "Following", false),
                node("followers_count", "45", "Followers", false),
                node("video_item", "", "Video 1", true),
                node("video_item", "", "Video 2", true));
        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(snapshot, 1_010L, 500L);
        check(profile.accessState.equals("available"), "access state");
        check(profile.username.equals("target_user"), "exact username");
        check(profile.following == 120L && profile.followers == 45L, "metrics");
        check(profile.videoCount == 2, "video count");
        check(profile.postHandles.equals(Arrays.asList("post:0", "post:1")), "posts");
        check(profile.followingResourceId.equals("following_count"), "following source");
        check(profile.followersResourceId.equals("followers_count"), "followers source");
    }

    private static void parsesAbbreviatedCounts() throws Exception {
        SemanticSnapshot snapshot = snapshot(
                node("username", "target_user", "", false),
                node("following_count", "1,234", "Following", false),
                node("followers_count", "2.5K", "Followers", false),
                node("video_item", "", "Video", true));
        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(snapshot, 1_010L, 500L);
        check(profile.following == 1_234L, "localized count");
        check(profile.followers == 2_500L, "abbreviated count");
    }

    private static void classifiesPrivateAndUnavailableProfiles() throws Exception {
        TikTokSemantics.Profile privateProfile = TikTokSemantics.parseProfile(
                snapshot(node("private_notice", "This account is private", "", false)),
                1_010L,
                500L);
        check(privateProfile.accessState.equals("private"), "private state");
        TikTokSemantics.Profile unavailable = TikTokSemantics.parseProfile(
                snapshot(node("status_notice", "Account not found", "", false)),
                1_010L,
                500L);
        check(unavailable.accessState.equals("unavailable"), "unavailable state");
    }

    private static void rejectsIncompleteAndStaleEvidence() {
        expectFailure(
                () -> TikTokSemantics.parseProfile(
                        snapshot(node("username", "target_user", "", false)), 1_010L, 500L),
                "incomplete_profile_evidence");
        expectFailure(
                () -> TikTokSemantics.parseProfile(
                        snapshotAt(10L, node("username", "target_user", "", false)),
                        1_010L,
                        500L),
                "stale_snapshot");
    }

    private static void selectsOnlyOneVisibleControl() throws Exception {
        SemanticSnapshot unique = snapshot(node("like_button", "", "Like", true));
        check(TikTokSemantics.uniqueControl(unique, "like").resourceId.equals("like_button"),
                "unique control");
        SemanticSnapshot hidden = snapshot(new SemanticSnapshot.Node(
                "like_button", "Button", "", "Like", new SemanticSnapshot.Bounds(0, 0, 10, 10),
                false, true, true, false, Collections.<SemanticSnapshot.Node>emptyList()));
        expectFailure(() -> TikTokSemantics.uniqueControl(hidden, "like"), "missing_control");
        SemanticSnapshot ambiguous = snapshot(
                node("like_button", "", "Like", true),
                node("like_button_2", "", "Like", true));
        expectFailure(() -> TikTokSemantics.uniqueControl(ambiguous, "like"), "ambiguous_control");
    }

    private static SemanticSnapshot snapshot(SemanticSnapshot.Node... nodes) throws Exception {
        return snapshotAt(1_000L, nodes);
    }

    private static SemanticSnapshot snapshotAt(long capturedAt, SemanticSnapshot.Node... nodes)
            throws Exception {
        SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                "root", "FrameLayout", "", "", new SemanticSnapshot.Bounds(0, 0, 1080, 1920),
                true, false, true, false, Arrays.asList(nodes));
        return SemanticSnapshot.fromRoot(root, 17L, capturedAt);
    }

    private static SemanticSnapshot.Node node(
            String resourceId, String text, String description, boolean clickable) {
        return new SemanticSnapshot.Node(
                resourceId, "TextView", text, description,
                new SemanticSnapshot.Bounds(0, 0, 100, 50), true, clickable, true, false,
                Collections.<SemanticSnapshot.Node>emptyList());
    }

    private static void expectFailure(ThrowingRunnable operation, String code) {
        try {
            operation.run();
            throw new AssertionError("expected failure " + code);
        } catch (TikTokSemantics.SemanticException error) {
            check(error.code.equals(code), "failure code " + code);
        } catch (Exception error) {
            throw new AssertionError("unexpected exception", error);
        }
    }

    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}
