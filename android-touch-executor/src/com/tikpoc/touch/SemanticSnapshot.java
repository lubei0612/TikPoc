package com.tikpoc.touch;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

public final class SemanticSnapshot {
    public static final int MAX_NODES = 4_096;

    public static final class Bounds {
        public final int left;
        public final int top;
        public final int right;
        public final int bottom;

        public Bounds(int left, int top, int right, int bottom) {
            this.left = left;
            this.top = top;
            this.right = right;
            this.bottom = bottom;
        }

        public boolean hasArea() {
            return right > left && bottom > top;
        }
    }

    public static final class Node {
        public final String resourceId;
        public final String className;
        public final String text;
        public final String description;
        public final Bounds bounds;
        public final boolean visible;
        public final boolean clickable;
        public final boolean enabled;
        public final boolean selected;
        public final List<Node> children;

        public Node(
                String resourceId,
                String className,
                String text,
                String description,
                Bounds bounds,
                boolean visible,
                boolean clickable,
                boolean enabled,
                boolean selected,
                List<Node> children) {
            this.resourceId = normalize(resourceId);
            this.className = normalize(className);
            this.text = normalize(text);
            this.description = normalize(description);
            this.bounds = bounds;
            this.visible = visible;
            this.clickable = clickable;
            this.enabled = enabled;
            this.selected = selected;
            this.children = Collections.unmodifiableList(new ArrayList<Node>(children));
        }

        public String searchableText() {
            return (text + " " + description).trim().toLowerCase(Locale.ROOT);
        }
    }

    public final Node root;
    public final List<Node> nodes;
    public final long eventSequence;
    public final long capturedAtElapsedMs;
    public final String digest;

    private SemanticSnapshot(
            Node root, List<Node> nodes, long eventSequence, long capturedAtElapsedMs,
            String digest) {
        this.root = root;
        this.nodes = Collections.unmodifiableList(nodes);
        this.eventSequence = eventSequence;
        this.capturedAtElapsedMs = capturedAtElapsedMs;
        this.digest = digest;
    }

    public static SemanticSnapshot fromRoot(
            Node root, long eventSequence, long capturedAtElapsedMs) throws SnapshotException {
        if (root == null || eventSequence < 0 || capturedAtElapsedMs < 0) {
            throw new SnapshotException("invalid_snapshot");
        }
        List<Node> nodes = new ArrayList<Node>();
        collect(root, nodes);
        return new SemanticSnapshot(
                root, nodes, eventSequence, capturedAtElapsedMs, digest(nodes));
    }

    public long ageMs(long nowElapsedMs) {
        return Math.max(0L, nowElapsedMs - capturedAtElapsedMs);
    }

    public static String normalize(String value) {
        if (value == null) return "";
        return value.replace('\u00a0', ' ').trim().replaceAll("\\s+", " ");
    }

    private static void collect(Node node, List<Node> nodes) throws SnapshotException {
        if (nodes.size() >= MAX_NODES) throw new SnapshotException("snapshot_too_large");
        nodes.add(node);
        for (Node child : node.children) collect(child, nodes);
    }

    private static String digest(List<Node> nodes) throws SnapshotException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Node node : nodes) {
                update(digest, node.resourceId);
                update(digest, node.className);
                update(digest, node.text);
                update(digest, node.description);
                update(digest, node.bounds.left + "," + node.bounds.top + ","
                        + node.bounds.right + "," + node.bounds.bottom);
                update(digest, node.visible + ":" + node.clickable + ":"
                        + node.enabled + ":" + node.selected);
            }
            StringBuilder encoded = new StringBuilder("sha256:");
            for (byte value : digest.digest()) encoded.append(String.format("%02x", value & 0xff));
            return encoded.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new SnapshotException("digest_unavailable");
        }
    }

    private static void update(MessageDigest digest, String value) {
        digest.update(value.getBytes(StandardCharsets.UTF_8));
        digest.update((byte) 0);
    }

    public static final class SnapshotException extends Exception {
        public final String code;

        public SnapshotException(String code) {
            super(code);
            this.code = code;
        }
    }
}
