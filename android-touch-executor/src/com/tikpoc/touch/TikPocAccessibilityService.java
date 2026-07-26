package com.tikpoc.touch;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.graphics.Rect;
import android.net.Uri;
import android.os.SystemClock;
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
                    elapsedRealtimeMs(), dispatcher::dispatch);
            AutonomousTaskExecutor executor = new AutonomousTaskExecutor(
                    ui, AutonomousTaskExecutor.Mode.SHADOW);
            AutonomousTaskRunner runner = new AutonomousTaskRunner(
                    client, store, settings.roundId, settings.sessionEpoch, executor);
            autonomousThread = new Thread(() -> {
                while (!Thread.currentThread().isInterrupted()) {
                    AutonomousTaskRunner.State state = runner.runOnce(elapsedRealtimeMs());
                    if (state == AutonomousTaskRunner.State.PAUSED) return;
                    try {
                        Thread.sleep(1_000L);
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
