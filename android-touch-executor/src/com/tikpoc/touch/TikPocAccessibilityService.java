package com.tikpoc.touch;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
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
            AutonomousTaskExecutor executor = new AutonomousTaskExecutor(
                    ui, settings.workerMode == DeviceProvisioning.WorkerMode.ACTIVE
                            ? AutonomousTaskExecutor.Mode.ACTIVE
                            : AutonomousTaskExecutor.Mode.SHADOW);
            AutonomousTaskRunner runner = new AutonomousTaskRunner(
                    client, store, settings.roundId, settings.sessionEpoch, executor);
            autonomousThread = new Thread(() -> {
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
    public String searchProfile(String username) throws Exception {
        Intent launch = getPackageManager().getLaunchIntentForPackage(TIKTOK_PACKAGE);
        if (launch == null) return "timeout";
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(launch);
        AccessibilityNodeInfo root = waitForRoot(4_000L);
        if (root == null) return "timeout";
        try {
            AccessibilityNodeInfo search = firstClickableByLabel(root, "search", "搜索");
            if (search == null || !search.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                recycle(search);
                return "timeout";
            }
            recycle(search);
        } finally {
            root.recycle();
        }
        AccessibilityNodeInfo inputRoot = waitForRoot(3_000L);
        if (inputRoot == null) return "timeout";
        try {
            AccessibilityNodeInfo input = firstEditable(inputRoot);
            if (input == null) return "timeout";
            Bundle text = new Bundle();
            text.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                    username);
            boolean entered = input.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text);
            input.recycle();
            if (!entered) return "timeout";
        } finally {
            inputRoot.recycle();
        }
        SystemClock.sleep(1_000L);
        AccessibilityNodeInfo results = waitForRoot(4_000L);
        if (results == null) return "timeout";
        try {
            List<AccessibilityNodeInfo> matches = new ArrayList<AccessibilityNodeInfo>();
            collectExactUsername(results, username, matches);
            if (matches.isEmpty()) return "no_match";
            if (matches.size() > 1) {
                recycleAll(matches);
                return "ambiguous";
            }
            AccessibilityNodeInfo candidate = matches.get(0);
            AccessibilityNodeInfo clickable = clickableAncestor(candidate);
            boolean clicked = clickable != null
                    && clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            recycle(clickable);
            candidate.recycle();
            return clicked ? "exact" : "timeout";
        } finally {
            results.recycle();
        }
    }

    private AccessibilityNodeInfo waitForRoot(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        AccessibilityNodeInfo root;
        while ((root = getRootInActiveWindow()) == null
                && SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(100L);
        return root;
    }

    private static AccessibilityNodeInfo firstEditable(AccessibilityNodeInfo node) {
        if (node.isVisibleToUser() && node.isEditable()) return AccessibilityNodeInfo.obtain(node);
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) continue;
            try {
                AccessibilityNodeInfo match = firstEditable(child);
                if (match != null) return match;
            } finally { child.recycle(); }
        }
        return null;
    }

    private static AccessibilityNodeInfo firstClickableByLabel(
            AccessibilityNodeInfo node, String... labels) {
        String text = (string(node.getText()) + " " + string(node.getContentDescription()))
                .toLowerCase(java.util.Locale.ROOT);
        if (node.isVisibleToUser() && node.isClickable()) {
            for (String label : labels) if (text.contains(label)) {
                return AccessibilityNodeInfo.obtain(node);
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
