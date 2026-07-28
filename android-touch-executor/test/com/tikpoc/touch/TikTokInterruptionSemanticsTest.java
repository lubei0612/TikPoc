package com.tikpoc.touch;

import java.util.Collections;
import java.util.Arrays;

public final class TikTokInterruptionSemanticsTest {
    public static void main(String[] args) throws Exception {
        classifiesNormalFeedAsNone();
        classifiesFriendDiscoveryAsOrdinaryDialog();
        classifiesLongPressMenu();
        classifiesChineseVerification();
        classifiesEnglishVerification();
        ignoresHiddenChallengeText();
        verifiesCreatorInsideVisibleControlDescription();
        selectsClickableParentOfExactHomeLabel();
        System.out.println("TikTokInterruptionSemanticsTest PASS");
    }

    private static void classifiesNormalFeedAsNone() throws Exception {
        check(classify(node("推荐", true)).equals("none"), "normal feed");
    }

    private static void classifiesFriendDiscoveryAsOrdinaryDialog() throws Exception {
        check(classify(node("与好友一起使用 TikTok 会更有趣", true))
                .equals("ordinary_dialog"), "friend discovery");
    }

    private static void classifiesLongPressMenu() throws Exception {
        check(classify(node("为什么看到此作品", true)).equals("long_press_menu"),
                "long press menu");
    }

    private static void classifiesChineseVerification() throws Exception {
        check(classify(node("请完成下列验证后继续:", true))
                .equals("verification_required"), "Chinese verification");
    }

    private static void classifiesEnglishVerification() throws Exception {
        check(classify(node("Verify to continue", true))
                .equals("verification_required"), "English verification");
    }

    private static void ignoresHiddenChallengeText() throws Exception {
        check(classify(node("Verify to continue", false)).equals("none"),
                "hidden verification ignored");
    }

    private static void verifiesCreatorInsideVisibleControlDescription() throws Exception {
        SemanticSnapshot.Node creator = new SemanticSnapshot.Node(
                "", "android.widget.Button", "", "@loveluxury.com · creator profile",
                new SemanticSnapshot.Bounds(0, 0, 300, 100), true, true, true, false,
                Collections.emptyList());
        SemanticSnapshot.Node caption = node(
                "A rare archive piece worth remembering", true);
        SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                "", "android.widget.FrameLayout", "", "",
                new SemanticSnapshot.Bounds(0, 0, 720, 1280), true, false, true, false,
                Arrays.asList(creator, caption));
        SemanticSnapshot snapshot = SemanticSnapshot.fromRoot(root, 1L, 100L);

        check(TikTokInterruptionSemantics.hasVisibleVideoIdentity(
                        snapshot, "loveluxury.com", "rare archive piece"),
                "creator token and caption anchor verified");
    }

    private static void selectsClickableParentOfExactHomeLabel() throws Exception {
        SemanticSnapshot.Node label = node("首页", true);
        SemanticSnapshot.Node control = new SemanticSnapshot.Node(
                "home-tab", "android.view.ViewGroup", "", "",
                new SemanticSnapshot.Bounds(0, 1100, 144, 1280), true, true, true, false,
                Collections.singletonList(label));
        SemanticSnapshot.Node root = new SemanticSnapshot.Node(
                "", "android.widget.FrameLayout", "", "",
                new SemanticSnapshot.Bounds(0, 0, 720, 1280), true, false, true, false,
                Collections.singletonList(control));
        SemanticSnapshot snapshot = SemanticSnapshot.fromRoot(root, 1L, 100L);

        check(TikTokInterruptionSemantics.uniqueClickableAncestorOfExactText(
                        snapshot, "首页") == control,
                "non-clickable exact label resolves to its clickable parent");
    }

    private static String classify(SemanticSnapshot.Node root) throws Exception {
        SemanticSnapshot snapshot = SemanticSnapshot.fromRoot(root, 1L, 100L);
        return TikTokInterruptionSemantics.classify(snapshot);
    }

    private static SemanticSnapshot.Node node(String text, boolean visible) {
        return new SemanticSnapshot.Node(
                "", "android.widget.TextView", text, "",
                new SemanticSnapshot.Bounds(0, 0, 720, 1280),
                visible, false, true, false, Collections.emptyList());
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
