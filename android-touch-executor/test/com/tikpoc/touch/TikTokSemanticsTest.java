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
        parsesChineseAbbreviatedCounts();
        parsesObfuscatedLiveProfileByExactIdentityAndGeometry();
        prefersAtHandleWhenDisplayNameMatchesUsername();
        classifiesPrivateAndUnavailableProfiles();
        rejectsIncompleteAndStaleEvidence();
        selectsOnlyOneVisibleControl();
        recognizesLocalizedObfuscatedVideoControls();
        recognizesLocalizedSelectedLikeState();
        recognizesSelectedFavoriteFromItsIconChild();
        recognizesLocalizedRepostConfirmation();
        selectsUniqueCommentPostControlBesideComposer();
        System.out.println("TikTokSemanticsTest PASS");
    }

    private static void recognizesSelectedFavoriteFromItsIconChild() throws Exception {
        SemanticSnapshot.Node selectedIcon = new SemanticSnapshot.Node(
                "g3n", "ImageView", "", "",
                new SemanticSnapshot.Bounds(10, 10, 90, 45), true, false, true, true,
                Collections.<SemanticSnapshot.Node>emptyList());
        SemanticSnapshot.Node favorite = new SemanticSnapshot.Node(
                "g4g", "Button", "", "将此视频添加到或移出收藏。",
                new SemanticSnapshot.Bounds(0, 0, 100, 50), true, true, true, false,
                Collections.singletonList(selectedIcon));

        check(TikTokSemantics.actionState(favorite).equals("on"),
                "selected favorite icon propagates to its control");
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

    private static void parsesChineseAbbreviatedCounts() throws Exception {
        SemanticSnapshot snapshot = snapshot(
                node("username", "target_user", "", false),
                node("following_count", "5,992", "Following", false),
                node("followers_count", "1.2 万", "Followers", false),
                node("video_item", "", "Video", true));
        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                snapshot, 1_010L, 500L);
        check(profile.followers == 12_000L, "Chinese ten-thousand count");
    }

    private static void parsesObfuscatedLiveProfileByExactIdentityAndGeometry()
            throws Exception {
        SemanticSnapshot snapshot = snapshot(
                nodeAt("oul", "@target_user", "", true, 264, 364, 456, 404, "Button"),
                nodeAt("bio", "@other_user", "", false, 20, 405, 300, 430, "TextView"),
                nodeAt("oti", "120", "", false, 59, 432, 259, 476, "TextView"),
                nodeAt("opr", "45", "", false, 326, 424, 394, 472, "TextView"),
                nodeAt("oti", "9", "", false, 461, 432, 661, 476, "TextView"),
                nodeAt("dp6", "", "", true, 1, 900, 359, 1375, "FrameLayout"),
                nodeAt("dp6", "", "", true, 361, 900, 719, 1375, "FrameLayout"));

        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                snapshot, 1_010L, 500L, "target_user");

        check(profile.username.equals("target_user"), "live exact username");
        check(profile.following == 120L && profile.followers == 45L, "live metrics");
        check(profile.postHandles.equals(Arrays.asList("post:0", "post:1")), "live posts");
        check(TikTokSemantics.postControl(snapshot, "post:1").resourceId.equals("dp6"),
                "live post control");
    }

    private static void prefersAtHandleWhenDisplayNameMatchesUsername() throws Exception {
        SemanticSnapshot snapshot = snapshot(
                nodeAt("", "target_user", "", true, 200, 320, 500, 364, "Button"),
                nodeAt("oul", "@target_user", "", true, 240, 364, 480, 404, "Button"),
                nodeAt("oti", "120", "", false, 59, 432, 259, 476, "TextView"),
                nodeAt("opr", "45", "", false, 326, 424, 394, 472, "TextView"),
                nodeAt("oti", "9", "", false, 461, 432, 661, 476, "TextView"),
                nodeAt("dp6", "", "", true, 1, 900, 359, 1375, "FrameLayout"));

        TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(
                snapshot, 1_010L, 500L, "target_user");

        check(profile.username.equals("target_user"), "at-handle identity preferred");
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

    private static void recognizesLocalizedObfuscatedVideoControls() throws Exception {
        SemanticSnapshot snapshot = snapshot(
                node("elq", "", "点赞视频。24 个赞", true),
                node("g4g", "", "将此视频添加到收藏。", true),
                node("elq", "", "分享视频。4 次分享", true),
                node("jmc", "", "转发", true));

        check(TikTokSemantics.hasVideoControls(snapshot), "localized video controls");
        check(TikTokSemantics.uniqueControl(snapshot, "like").resourceId.equals("elq"),
                "localized like");
        check(TikTokSemantics.uniqueControl(snapshot, "favorite").resourceId.equals("g4g"),
                "localized favorite");
        check(TikTokSemantics.uniqueControl(snapshot, "share").resourceId.equals("elq"),
                "localized share");
        check(TikTokSemantics.uniqueControl(snapshot, "repost").resourceId.equals("jmc"),
                "localized repost");
    }

    private static void recognizesLocalizedSelectedLikeState() throws Exception {
        SemanticSnapshot.Node liked = node("elq", "", "点赞的视频", true);
        SemanticSnapshot snapshot = snapshot(liked);

        check(TikTokSemantics.uniqueControl(snapshot, "like") == liked,
                "localized selected like control");
        check(TikTokSemantics.actionState(liked).equals("on"),
                "localized selected like state");
    }

    private static void recognizesLocalizedRepostConfirmation() throws Exception {
        check(TikTokSemantics.hasRepostConfirmation(
                snapshot(node("w1a", "你已转发", "", false))),
                "localized repost confirmation");
        check(TikTokSemantics.repostConfirmation(
                snapshot(node("w1a", "你已转发", "", false))).resourceId.equals("w1a"),
                "repost confirmation source");
        check(!TikTokSemantics.hasRepostConfirmation(
                snapshot(node("jmc", "", "转发", true))),
                "repost control is not confirmation");
    }

    private static void selectsUniqueCommentPostControlBesideComposer() throws Exception {
        SemanticSnapshot snapshot = snapshot(
                nodeAt("dbq", "Draft comment", "", true,
                        144, 1170, 578, 1238, "EditText"),
                nodeAt("de5", "", "@2131889388", true,
                        586, 1192, 678, 1248, "Button"),
                nodeAt("close", "", "Close", true,
                        656, 438, 696, 478, "ImageView"));

        check(TikTokSemantics.commentPostControl(snapshot).resourceId.equals("de5"),
                "post control selected beside composer");
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

    private static SemanticSnapshot.Node nodeAt(
            String resourceId, String text, String description, boolean clickable,
            int left, int top, int right, int bottom, String className) {
        return new SemanticSnapshot.Node(
                resourceId, className, text, description,
                new SemanticSnapshot.Bounds(left, top, right, bottom), true, clickable, true,
                false, Collections.<SemanticSnapshot.Node>emptyList());
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
