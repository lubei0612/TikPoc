package com.tikpoc.touch;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.graphics.Path;
import android.graphics.Rect;
import android.net.Uri;
import android.os.SystemClock;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

public final class TikPocAccessibilityService extends AccessibilityService
        implements TouchCommandDispatcher.SnapshotSource,
        TouchCommandDispatcher.Actuator, TouchCommandDispatcher.Clock,
        TouchCommandDispatcher.SurfaceSource {
    private static final String TIKTOK_PACKAGE = "com.zhiliaoapp.musically";
    private final AtomicLong eventSequence = new AtomicLong();
    private volatile SemanticSnapshot snapshot;
    private volatile String activityName = "";
    private volatile String packageName = "";
    private LoopbackCommandServer server;
    private Thread autonomousThread;

    @Override
    protected void onServiceConnected() {
        refreshSnapshot();
        try {
            TouchCommandDispatcher dispatcher = new TouchCommandDispatcher(
                    this, this, this, this);
            startAutonomousWorker(dispatcher);
            CommandGate gate = new CommandGate(this::elapsedRealtimeMs);
            server = new LoopbackCommandServer("127.0.0.1", 47101, gate, dispatcher, this);
            server.start();
        } catch (Exception error) {
            android.util.Log.e("TikPocTouch", "helper startup failed");
            disableSelf();
        }
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        eventSequence.incrementAndGet();
        if (event != null && event.getClassName() != null) {
            activityName = event.getClassName().toString();
        }
        if (event != null && event.getPackageName() != null) {
            packageName = event.getPackageName().toString();
        }
        refreshSnapshot();
        synchronized (this) {
            notifyAll();
        }
    }

    @Override
    public void onInterrupt() {}

    @Override
    public void onDestroy() {
        if (autonomousThread != null) {
            autonomousThread.interrupt();
            try {
                autonomousThread.join(2_000L);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            autonomousThread = null;
        }
        if (server != null) {
            try {
                server.close();
            } catch (Exception error) {
                android.util.Log.w("TikPocTouch", "helper shutdown failed");
            }
        }
        super.onDestroy();
    }

    private void startAutonomousWorker(TouchCommandDispatcher dispatcher) {
        try {
            DeviceProvisioning.Settings settings = DeviceProvisioning.load(
                    new AndroidProvisioningStore(this), new AndroidTokenVault(this));
            if (settings == null) return;
            DeviceTaskStore store = new DeviceTaskStore(new AndroidTaskBackend(this));
            DeviceApiClient client = new DeviceApiClient(
                    settings.baseUrl, settings.deviceId, settings.accessToken,
                    settings.sessionEpoch, new DeviceApiClient.HttpsExchange());
            AccessibilityUiAdapter ui = new AccessibilityUiAdapter(
                    settings.deviceId, settings.accountId, settings.sessionEpoch,
                    this::elapsedRealtimeMs, dispatcher::dispatch);
            CommentTaskExecutor commentExecutor = new CommentTaskExecutor(
                    ui, store::checkpoint);
            AutonomousTaskExecutor executor = new AutonomousTaskExecutor(
                    ui, settings.workerMode == DeviceProvisioning.WorkerMode.ACTIVE
                            ? AutonomousTaskExecutor.Mode.ACTIVE
                            : AutonomousTaskExecutor.Mode.SHADOW,
                    commentExecutor);
            AutonomousTaskRunner runner = new AutonomousTaskRunner(
                    client, store, settings.roundId, settings.sessionEpoch, executor);
            autonomousThread = new Thread(() -> {
                if ("brand_comment".equals(settings.roundId)) {
                    try {
                        ui.recoverHome();
                        client.heartbeat(
                                "1.0.0", "stable_home",
                                store.queueDepth(
                                        settings.sessionEpoch, System.currentTimeMillis()),
                                System.currentTimeMillis());
                    } catch (Exception recoveryBlocked) {
                        return;
                    }
                }
                while (!Thread.currentThread().isInterrupted()) {
                    AutonomousTaskRunner.State state = runner.runOnce(System.currentTimeMillis());
                    if (state == AutonomousTaskRunner.State.PAUSED) return;
                    try {
                        Thread.sleep(runner.recommendedDelayMs());
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                    }
                }
            }, "tikpoc-autonomous-mobile-worker");
            autonomousThread.start();
        } catch (Exception error) {
            android.util.Log.w("TikPocTouch", "autonomous worker not started");
        }
    }

    @Override
    public SemanticSnapshot current() throws Exception {
        refreshSnapshot();
        SemanticSnapshot observed = snapshot;
        if (observed == null) throw new IllegalStateException("accessibility tree unavailable");
        return observed;
    }

    @Override
    public synchronized SemanticSnapshot awaitAfter(long sequence, long timeoutMs)
            throws Exception {
        long deadline = SystemClock.elapsedRealtime() + Math.max(0L, timeoutMs);
        while (snapshot != null && snapshot.eventSequence <= sequence) {
            long remaining = deadline - SystemClock.elapsedRealtime();
            if (remaining <= 0L) break;
            wait(remaining);
        }
        if (snapshot == null) throw new IllegalStateException("accessibility tree unavailable");
        return snapshot;
    }

    @Override
    public boolean click(SemanticSnapshot.Node expected) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        try {
            List<AccessibilityNodeInfo> matches = new ArrayList<AccessibilityNodeInfo>();
            collectMatches(root, expected, matches);
            if (matches.size() != 1) {
                for (AccessibilityNodeInfo match : matches) match.recycle();
                return false;
            }
            boolean clicked = matches.get(0).performAction(AccessibilityNodeInfo.ACTION_CLICK);
            matches.get(0).recycle();
            return clicked;
        } finally {
            root.recycle();
        }
    }

    @Override
    public boolean openProfile(String route) {
        Uri uri = Uri.parse(route);
        if (!("https".equals(uri.getScheme()) || "snssdk1233".equals(uri.getScheme()))) {
            return false;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, uri)
                .setPackage(TIKTOK_PACKAGE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
        return true;
    }

    @Override
    public boolean browseHomeReadOnly() {
        AccessibilityNodeInfo root = waitForRoot(2_000L);
        if (root == null) return false;
        AccessibilityNodeInfo home = null;
        try {
            home = firstClickableByExactText(root, "首页");
            if (home == null) home = firstClickableByExactText(root, "Home");
            if (home == null || !home.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return false;
            }
        } finally {
            recycle(home);
            root.recycle();
        }
        SystemClock.sleep(400L);
        Path path = new Path();
        path.moveTo(540F, 1_350F);
        path.lineTo(540F, 750F);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0L, 350L))
                .build();
        return dispatchGesture(gesture, null, null);
    }

    @Override
    public boolean dismissOrdinaryInterruption(String kind) {
        if (TikTokInterruptionSemantics.LONG_PRESS_MENU.equals(kind)) {
            return performGlobalAction(GLOBAL_ACTION_BACK);
        }
        if (!TikTokInterruptionSemantics.ORDINARY_DIALOG.equals(kind)) return false;
        AccessibilityNodeInfo root = waitForRoot(1_000L);
        if (root == null) return false;
        AccessibilityNodeInfo dismiss = null;
        try {
            String[] labels = {"不允许", "Not now", "关闭", "Close"};
            for (String label : labels) {
                dismiss = firstClickableByExactText(root, label);
                if (dismiss != null) break;
            }
            if (dismiss != null) {
                return dismiss.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            }
        } finally {
            recycle(dismiss);
            root.recycle();
        }
        return performGlobalAction(GLOBAL_ACTION_BACK);
    }

    @Override
    public boolean resetVerification() {
        boolean first = performGlobalAction(GLOBAL_ACTION_BACK);
        SystemClock.sleep(250L);
        boolean second = performGlobalAction(GLOBAL_ACTION_BACK);
        return first && second;
    }

    @Override
    public boolean returnToHome() {
        if (!TIKTOK_PACKAGE.equals(packageName)) {
            Intent launch = getPackageManager().getLaunchIntentForPackage(TIKTOK_PACKAGE);
            if (launch == null) return false;
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(launch);
            SystemClock.sleep(400L);
        }
        AccessibilityNodeInfo root = waitForRoot(2_000L);
        if (root == null) return false;
        AccessibilityNodeInfo home = null;
        try {
            home = firstClickableByExactText(root, "首页");
            if (home == null) home = firstClickableByExactText(root, "Home");
            return home != null && home.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        } finally {
            recycle(home);
            root.recycle();
        }
    }

    @Override
    public boolean openCommentVideo(String videoId, String videoUrl) {
        Uri uri = Uri.parse(videoUrl);
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase();
        if (!"https".equals(uri.getScheme())
                || !(host.equals("tiktok.com") || host.endsWith(".tiktok.com"))
                || !uri.getPath().matches(".*/video/" + java.util.regex.Pattern.quote(videoId))) {
            return false;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, uri)
                .setPackage(TIKTOK_PACKAGE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
        return true;
    }

    @Override
    public boolean submitFirstLevelComment(String text) {
        AccessibilityNodeInfo root = waitForRoot(2_000L);
        if (root == null) return false;
        AccessibilityNodeInfo comments = null;
        try {
            comments = firstClickableByLabel(root, "comments", "评论");
            if (comments == null
                    || !comments.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return false;
        } finally {
            recycle(comments);
            root.recycle();
        }
        SystemClock.sleep(300L);
        AccessibilityNodeInfo composer = firstEditable(2_000L);
        if (composer == null) return false;
        try {
            Bundle input = new Bundle();
            input.putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            if (!composer.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, input)) return false;
        } finally {
            composer.recycle();
        }
        AccessibilityNodeInfo commentRoot = waitForRoot(1_000L);
        if (commentRoot == null) return false;
        AccessibilityNodeInfo post = null;
        try {
            String[] labels = {"Post", "Send", "发布", "发送"};
            for (String label : labels) {
                post = firstClickableByExactText(commentRoot, label);
                if (post != null) break;
            }
            return post != null && post.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        } finally {
            recycle(post);
            commentRoot.recycle();
        }
    }

    private AccessibilityNodeInfo firstEditable(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < deadline) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                try {
                    AccessibilityNodeInfo editable = firstVisibleEditable(root);
                    if (editable != null) return editable;
                } finally {
                    root.recycle();
                }
            }
            SystemClock.sleep(100L);
        }
        return null;
    }

    private static AccessibilityNodeInfo firstVisibleEditable(AccessibilityNodeInfo node) {
        if (node.isVisibleToUser() && node.isEditable()) {
            return AccessibilityNodeInfo.obtain(node);
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try {
                AccessibilityNodeInfo match = firstVisibleEditable(child);
                if (match != null) return match;
            } finally {
                child.recycle();
            }
        }
        return null;
    }

    @Override
    public String searchProfile(String username) throws Exception {
        Intent launch = getPackageManager().getLaunchIntentForPackage(TIKTOK_PACKAGE);
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(launch);
        } else if (!TIKTOK_PACKAGE.equals(packageName)) {
            return "search_launch_unavailable";
        }
        AccessibilityNodeInfo input = openSearchInput();
        if (input == null) return "search_input_missing";
        boolean submitted = false;
        try {
            input.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
            Bundle text = new Bundle();
            text.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                    username);
            boolean entered = input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text);
            if (!entered) return "search_text_rejected";
            SystemClock.sleep(200L);
            submitted = input.performAction(
                    AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
        } finally {
            input.recycle();
        }
        boolean visibleSubmit = clickExactSearchSubmit(800L);
        if (!visibleSubmit) visibleSubmit = clickSearchSubmit(2_200L);
        if (!submitted && !visibleSubmit) {
            return "search_submit_missing";
        }
        if (!clickSearchUsersTab(3_000L)) return "search_users_tab_missing";
        return waitForAndClickExactUsername(username, 12_000L);
    }

    private AccessibilityNodeInfo openSearchInput() {
        for (int attempt = 0; attempt < 4; attempt++) {
            AccessibilityNodeInfo root = waitForRoot(2_000L);
            if (root == null) continue;
            Rect searchBounds = new Rect();
            try {
                AccessibilityNodeInfo input = firstSearchEditable(root);
                if (input != null) {
                    AccessibilityNodeInfo submit = firstClickableByExactText(root, "搜索");
                    if (submit == null) {
                        submit = firstClickableByExactText(root, "Search");
                    }
                    if (submit != null) {
                        submit.recycle();
                        return input;
                    }
                    Rect inputBounds = new Rect();
                    input.getBoundsInScreen(inputBounds);
                    boolean opened = tapCenter(inputBounds)
                            || input.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                    input.recycle();
                    if (opened) {
                        SystemClock.sleep(300L);
                        continue;
                    }
                }
                AccessibilityNodeInfo search = firstClickableByLabel(root, "search", "搜索");
                if (search != null) {
                    search.getBoundsInScreen(searchBounds);
                    search.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                    input = waitForSearchEditable(2_000L);
                    if (input == null && tapCenter(searchBounds)) {
                        input = waitForSearchEditable(2_000L);
                    }
                    recycle(search);
                    search = null;
                    if (input != null) return input;
                }
                recycle(search);
            } finally {
                root.recycle();
            }
            performGlobalAction(GLOBAL_ACTION_BACK);
            SystemClock.sleep(300L);
        }
        return null;
    }

    private AccessibilityNodeInfo waitForRoot(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        AccessibilityNodeInfo root;
        while ((root = getRootInActiveWindow()) == null
                && SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(100L);
        return root;
    }

    private boolean tapCenter(Rect bounds) {
        if (bounds == null || bounds.isEmpty()) return false;
        if (bounds.exactCenterX() < 0 || bounds.exactCenterY() < 0) return false;
        Path path = new Path();
        path.moveTo(bounds.exactCenterX(), bounds.exactCenterY());
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0L, 80L))
                .build();
        return dispatchGesture(gesture, null, null);
    }

    private AccessibilityNodeInfo waitForSearchEditable(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < deadline) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                try {
                    AccessibilityNodeInfo input = firstSearchEditable(root);
                    if (input != null) return input;
                } finally { root.recycle(); }
            }
            SystemClock.sleep(100L);
        }
        return null;
    }

    private String waitForAndClickExactUsername(String username, long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < deadline) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                try {
                    List<AccessibilityNodeInfo> matches =
                            new ArrayList<AccessibilityNodeInfo>();
                    collectExactUsername(root, username, matches);
                    if (matches.size() > 1) {
                        recycleAll(matches);
                        return "ambiguous";
                    }
                    if (matches.size() == 1) {
                        AccessibilityNodeInfo candidate = matches.get(0);
                        AccessibilityNodeInfo clickable = clickableAncestor(candidate);
                        Rect bounds = new Rect();
                        candidate.getBoundsInScreen(bounds);
                        boolean clicked = tapCenter(bounds);
                        if (clicked) {
                            SystemClock.sleep(600L);
                            AccessibilityNodeInfo stillSearching =
                                    waitForSearchEditable(200L);
                            if (stillSearching != null) {
                                stillSearching.recycle();
                                clicked = (clickable != null && clickable.performAction(
                                        AccessibilityNodeInfo.ACTION_CLICK)) || clicked;
                            }
                        }
                        if (!clicked && clickable != null) {
                            clicked = clickable.performAction(
                                    AccessibilityNodeInfo.ACTION_CLICK);
                        }
                        recycle(clickable);
                        candidate.recycle();
                        if (clicked) SystemClock.sleep(300L);
                        return clicked ? "exact" : "timeout";
                    }
                } finally { root.recycle(); }
            }
            SystemClock.sleep(100L);
        }
        return "no_match";
    }

    private boolean clickSearchSubmit(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < deadline) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                try {
                    AccessibilityNodeInfo submit = firstClickableByExactText(root, "搜索");
                    if (submit == null) {
                        submit = firstClickableByLabel(root, "search", "搜索");
                    }
                    if (submit != null) {
                        Rect bounds = new Rect();
                        submit.getBoundsInScreen(bounds);
                        boolean clicked = submit.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                        submit.recycle();
                        if (clicked || tapCenter(bounds)) return true;
                    }
                } finally { root.recycle(); }
            }
            SystemClock.sleep(100L);
        }
        return false;
    }

    private boolean clickExactSearchSubmit(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < deadline) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                try {
                    AccessibilityNodeInfo submit = firstClickableByExactText(root, "搜索");
                    if (submit == null) {
                        submit = firstClickableByExactText(root, "Search");
                    }
                    if (submit != null) {
                        Rect bounds = new Rect();
                        submit.getBoundsInScreen(bounds);
                        boolean clicked = tapCenter(bounds)
                                || submit.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                        submit.recycle();
                        if (clicked) return true;
                    }
                } finally { root.recycle(); }
            }
            SystemClock.sleep(100L);
        }
        return false;
    }

    private boolean clickSearchUsersTab(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < deadline) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                try {
                    AccessibilityNodeInfo tab = firstClickableByExactText(root, "用户");
                    if (tab == null) tab = firstClickableByExactText(root, "Users");
                    if (tab != null) {
                        Rect bounds = new Rect();
                        tab.getBoundsInScreen(bounds);
                        boolean clicked = tapCenter(bounds)
                                || tab.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                        tab.recycle();
                        if (clicked) {
                            SystemClock.sleep(300L);
                            return true;
                        }
                    }
                } finally { root.recycle(); }
            }
            SystemClock.sleep(100L);
        }
        return false;
    }

    private static AccessibilityNodeInfo firstClickableByExactText(
            AccessibilityNodeInfo node, String expected) {
        if (node.isVisibleToUser() && string(node.getText()).trim().equals(expected)) {
            return AccessibilityNodeInfo.obtain(node);
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try {
                AccessibilityNodeInfo match = firstClickableByExactText(child, expected);
                if (match != null) return match;
            } finally { child.recycle(); }
        }
        return null;
    }

    private static AccessibilityNodeInfo firstSearchEditable(AccessibilityNodeInfo node) {
        if (node.isVisibleToUser() && node.isEditable()
                && string(node.getViewIdResourceName()).endsWith(":id/fu9")) {
            return AccessibilityNodeInfo.obtain(node);
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try {
                AccessibilityNodeInfo match = firstSearchEditable(child);
                if (match != null) return match;
            } finally { child.recycle(); }
        }
        return null;
    }

    private static AccessibilityNodeInfo firstClickableByLabel(
            AccessibilityNodeInfo node, String... labels) {
        String text = (string(node.getText()) + " " + string(node.getContentDescription()))
                .toLowerCase(java.util.Locale.ROOT);
        if (node.isVisibleToUser()) {
            for (String label : labels) if (text.contains(label)) {
                return clickableAncestor(node);
            }
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try {
                AccessibilityNodeInfo match = firstClickableByLabel(child, labels);
                if (match != null) return match;
            } finally { child.recycle(); }
        }
        return null;
    }

    private static void collectExactUsername(
            AccessibilityNodeInfo node, String username, List<AccessibilityNodeInfo> matches) {
        if (node.isVisibleToUser() && !node.isEditable()
                && TikTokSearchSemantics.normalizeUsername(string(node.getText())).equals(
                        TikTokSearchSemantics.normalizeUsername(username))) {
            matches.add(AccessibilityNodeInfo.obtain(node));
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try { collectExactUsername(child, username, matches); }
            finally { child.recycle(); }
        }
    }

    private static AccessibilityNodeInfo clickableAncestor(AccessibilityNodeInfo node) {
        AccessibilityNodeInfo current = AccessibilityNodeInfo.obtain(node);
        while (current != null && !current.isClickable()) {
            AccessibilityNodeInfo parent = current.getParent();
            current.recycle();
            current = parent;
        }
        return current;
    }

    private static void recycle(AccessibilityNodeInfo node) {
        if (node != null) node.recycle();
    }

    private static void recycleAll(List<AccessibilityNodeInfo> nodes) {
        for (AccessibilityNodeInfo node : nodes) node.recycle();
    }

    @Override
    public long elapsedRealtimeMs() {
        return SystemClock.elapsedRealtime();
    }

    @Override
    public String packageName() {
        return packageName;
    }

    @Override
    public String activityName() {
        return activityName;
    }

    private synchronized void refreshSnapshot() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return;
        try {
            snapshot = SemanticSnapshot.fromRoot(
                    copyNode(root), eventSequence.get(), SystemClock.elapsedRealtime());
        } catch (Exception error) {
            snapshot = null;
        } finally {
            root.recycle();
        }
    }

    private static SemanticSnapshot.Node copyNode(AccessibilityNodeInfo source)
            throws SemanticSnapshot.SnapshotException {
        Rect rect = new Rect();
        source.getBoundsInScreen(rect);
        List<SemanticSnapshot.Node> children = new ArrayList<SemanticSnapshot.Node>();
        for (int index = 0; index < source.getChildCount(); index++) {
            AccessibilityNodeInfo child = source.getChild(index);
            if (child == null) continue;
            try {
                children.add(copyNode(child));
            } finally {
                child.recycle();
            }
        }
        return new SemanticSnapshot.Node(
                string(source.getViewIdResourceName()), string(source.getClassName()),
                string(source.getText()), string(source.getContentDescription()),
                new SemanticSnapshot.Bounds(rect.left, rect.top, rect.right, rect.bottom),
                source.isVisibleToUser(), source.isClickable(), source.isEnabled(),
                source.isSelected() || source.isChecked(), children);
    }

    private static void collectMatches(
            AccessibilityNodeInfo node, SemanticSnapshot.Node expected,
            List<AccessibilityNodeInfo> matches) {
        Rect rect = new Rect();
        node.getBoundsInScreen(rect);
        if (node.isVisibleToUser() && node.isEnabled() && node.isClickable()
                && string(node.getViewIdResourceName()).equals(expected.resourceId)
                && rect.left == expected.bounds.left && rect.top == expected.bounds.top
                && rect.right == expected.bounds.right && rect.bottom == expected.bounds.bottom) {
            matches.add(AccessibilityNodeInfo.obtain(node));
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try {
                collectMatches(child, expected, matches);
            } finally {
                child.recycle();
            }
        }
    }

    private static String string(CharSequence value) {
        return value == null ? "" : value.toString();
    }
}
