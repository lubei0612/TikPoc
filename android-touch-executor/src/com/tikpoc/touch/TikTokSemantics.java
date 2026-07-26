package com.tikpoc.touch;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

public final class TikTokSemantics {
    private TikTokSemantics() {}

    public static final class SemanticException extends Exception {
        public final String code;

        public SemanticException(String code) {
            super(code);
            this.code = code;
        }
    }

    public static final class Profile {
        public final String accessState;
        public final String username;
        public final long following;
        public final long followers;
        public final int videoCount;
        public final List<String> postHandles;
        public final String followingResourceId;
        public final String followersResourceId;
        public final long eventSequence;
        public final String evidenceDigest;

        private Profile(
                String accessState, String username, long following, long followers,
                List<String> postHandles, String followingResourceId,
                String followersResourceId, SemanticSnapshot snapshot) {
            this.accessState = accessState;
            this.username = username;
            this.following = following;
            this.followers = followers;
            this.videoCount = postHandles.size();
            this.postHandles = Collections.unmodifiableList(postHandles);
            this.followingResourceId = followingResourceId;
            this.followersResourceId = followersResourceId;
            this.eventSequence = snapshot.eventSequence;
            this.evidenceDigest = snapshot.digest;
        }

        private static Profile terminal(String accessState, SemanticSnapshot snapshot) {
            return new Profile(
                    accessState, "", 0L, 0L, Collections.<String>emptyList(), "", "", snapshot);
        }
    }

    public static Profile parseProfile(
            SemanticSnapshot snapshot, long nowElapsedMs, long maxTreeAgeMs)
            throws SemanticException {
        return parseProfile(snapshot, nowElapsedMs, maxTreeAgeMs, "");
    }

    public static Profile parseProfile(
            SemanticSnapshot snapshot, long nowElapsedMs, long maxTreeAgeMs,
            String expectedUsername) throws SemanticException {
        if (snapshot.ageMs(nowElapsedMs) > maxTreeAgeMs) {
            throw new SemanticException("stale_snapshot");
        }
        if (containsVisible(snapshot, "account is private")) {
            return Profile.terminal("private", snapshot);
        }
        if (containsVisible(snapshot, "account not found")
                || containsVisible(snapshot, "account doesn't exist")
                || containsVisible(snapshot, "account has been suspended")) {
            return Profile.terminal("unavailable", snapshot);
        }
        SemanticSnapshot.Node usernameNode = expectedUsername.isEmpty()
                ? uniqueByResource(snapshot, "username")
                : uniqueExactUsername(snapshot, expectedUsername);
        SemanticSnapshot.Node followingNode = uniqueByResource(snapshot, "following_count");
        SemanticSnapshot.Node followersNode = uniqueByResource(snapshot, "followers_count");
        if (followingNode == null || followersNode == null) {
            List<SemanticSnapshot.Node> counts = profileHeaderCounts(snapshot);
            if (counts.size() == 3) {
                followingNode = counts.get(0);
                followersNode = counts.get(1);
            }
        }
        if (usernameNode == null || followingNode == null || followersNode == null) {
            throw new SemanticException("incomplete_profile_evidence");
        }
        String username = normalizedUsername(usernameNode.text);
        if (username.isEmpty()) throw new SemanticException("incomplete_profile_evidence");
        List<SemanticSnapshot.Node> posts = postNodes(snapshot);
        List<String> handles = new ArrayList<String>();
        for (int index = 0; index < posts.size(); index++) handles.add("post:" + index);
        return new Profile(
                "available", username, parseCount(followingNode.text),
                parseCount(followersNode.text), handles, followingNode.resourceId,
                followersNode.resourceId, snapshot);
    }

    public static SemanticSnapshot.Node uniqueControl(
            SemanticSnapshot snapshot, String action) throws SemanticException {
        String normalizedAction = SemanticSnapshot.normalize(action).toLowerCase(Locale.ROOT);
        List<SemanticSnapshot.Node> matches = new ArrayList<SemanticSnapshot.Node>();
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            String resource = node.resourceId.toLowerCase(Locale.ROOT);
            if (node.visible && node.enabled && node.clickable && node.bounds.hasArea()
                    && (resource.contains(normalizedAction)
                    || node.searchableText().equals(normalizedAction)
                    || node.searchableText().startsWith(normalizedAction + " ")
                    || localizedActionMatches(node.searchableText(), normalizedAction))) {
                matches.add(node);
            }
        }
        if (matches.isEmpty()) throw new SemanticException("missing_control");
        if (matches.size() != 1) throw new SemanticException("ambiguous_control");
        return matches.get(0);
    }

    public static String actionState(SemanticSnapshot.Node node) {
        if (hasSelectedNode(node)) return "on";
        String searchable = node.searchableText();
        if (searchable.contains("selected") || searchable.contains("remove")
                || searchable.contains("点赞的视频")) return "on";
        return "off";
    }

    private static boolean hasSelectedNode(SemanticSnapshot.Node node) {
        if (node.selected) return true;
        for (SemanticSnapshot.Node child : node.children) {
            if (hasSelectedNode(child)) return true;
        }
        return false;
    }

    public static SemanticSnapshot.Node postControl(
            SemanticSnapshot snapshot, String videoKey) throws SemanticException {
        if (videoKey == null || !videoKey.startsWith("post:")) {
            throw new SemanticException("invalid_post_handle");
        }
        int expectedIndex;
        try {
            expectedIndex = Integer.parseInt(videoKey.substring(5));
        } catch (NumberFormatException error) {
            throw new SemanticException("invalid_post_handle");
        }
        if (expectedIndex < 0) throw new SemanticException("invalid_post_handle");
        List<SemanticSnapshot.Node> posts = postNodes(snapshot);
        if (expectedIndex < posts.size()) return posts.get(expectedIndex);
        throw new SemanticException("missing_post_handle");
    }

    private static SemanticSnapshot.Node uniqueExactUsername(
            SemanticSnapshot snapshot, String expectedUsername) throws SemanticException {
        String expected = normalizedUsername(expectedUsername).toLowerCase(Locale.ROOT);
        SemanticSnapshot.Node found = null;
        SemanticSnapshot.Node handle = null;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()) continue;
            String observed = normalizedUsername(node.text).toLowerCase(Locale.ROOT);
            if (!observed.equals(expected)) continue;
            if (node.text.trim().startsWith("@")) {
                if (handle != null) throw new SemanticException("ambiguous_profile_evidence");
                handle = node;
                continue;
            }
            if (found != null) throw new SemanticException("ambiguous_profile_evidence");
            found = node;
        }
        return handle != null ? handle : found;
    }

    private static List<SemanticSnapshot.Node> profileHeaderCounts(
            SemanticSnapshot snapshot) {
        int height = viewportHeight(snapshot);
        List<SemanticSnapshot.Node> counts = new ArrayList<SemanticSnapshot.Node>();
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()
                    || !node.className.endsWith("TextView")
                    || node.bounds.top < height * 20 / 100
                    || node.bounds.bottom > height * 45 / 100) continue;
            try {
                parseCount(node.text);
                counts.add(node);
            } catch (SemanticException ignored) {
                // Non-count profile header text is not metric evidence.
            }
        }
        Collections.sort(counts, Comparator.comparingInt(node -> node.bounds.left));
        return counts;
    }

    private static List<SemanticSnapshot.Node> postNodes(SemanticSnapshot snapshot) {
        int width = viewportWidth(snapshot);
        int height = viewportHeight(snapshot);
        List<SemanticSnapshot.Node> posts = new ArrayList<SemanticSnapshot.Node>();
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.clickable || !node.bounds.hasArea()) {
                continue;
            }
            String resource = node.resourceId.toLowerCase(Locale.ROOT);
            int nodeWidth = node.bounds.right - node.bounds.left;
            boolean geometry = node.className.endsWith("FrameLayout")
                    && node.bounds.top >= height * 45 / 100
                    && nodeWidth >= width * 28 / 100
                    && nodeWidth <= width * 38 / 100;
            if (resource.contains("video_item") || geometry) posts.add(node);
        }
        Collections.sort(posts, Comparator
                .comparingInt((SemanticSnapshot.Node node) -> node.bounds.top)
                .thenComparingInt(node -> node.bounds.left));
        return posts;
    }

    private static int viewportWidth(SemanticSnapshot snapshot) {
        int width = 0;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            width = Math.max(width, node.bounds.right - node.bounds.left);
        }
        return width;
    }

    private static int viewportHeight(SemanticSnapshot snapshot) {
        int height = 0;
        for (SemanticSnapshot.Node node : snapshot.nodes) height = Math.max(height, node.bounds.bottom);
        return height;
    }

    public static boolean hasVideoControls(SemanticSnapshot snapshot) {
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()) continue;
            String resource = node.resourceId.toLowerCase(Locale.ROOT);
            if (resource.contains("like") || resource.contains("favorite")
                    || resource.contains("share")
                    || localizedActionMatches(node.searchableText(), "like")
                    || localizedActionMatches(node.searchableText(), "favorite")
                    || localizedActionMatches(node.searchableText(), "share")) return true;
        }
        return false;
    }

    public static boolean hasRepostConfirmation(SemanticSnapshot snapshot) {
        return repostConfirmation(snapshot) != null;
    }

    public static SemanticSnapshot.Node repostConfirmation(SemanticSnapshot snapshot) {
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (node.visible && node.enabled && node.bounds.hasArea()
                    && (node.searchableText().contains("you reposted")
                    || node.searchableText().contains("你已转发"))) return node;
        }
        return null;
    }

    private static boolean localizedActionMatches(String searchable, String action) {
        if (action.equals("like")) {
            return searchable.contains("点赞视频") || searchable.contains("点赞的视频");
        }
        if (action.equals("favorite")) return searchable.contains("收藏");
        if (action.equals("share")) return searchable.contains("分享视频");
        if (action.equals("repost")) return searchable.equals("转发");
        return false;
    }

    private static SemanticSnapshot.Node uniqueByResource(
            SemanticSnapshot snapshot, String fragment) throws SemanticException {
        SemanticSnapshot.Node found = null;
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (!node.visible || !node.enabled || !node.bounds.hasArea()
                    || !node.resourceId.toLowerCase(Locale.ROOT).contains(fragment)) continue;
            if (found != null) throw new SemanticException("ambiguous_profile_evidence");
            found = node;
        }
        return found;
    }

    private static boolean containsVisible(SemanticSnapshot snapshot, String phrase) {
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (node.visible && node.searchableText().contains(phrase)) return true;
        }
        return false;
    }

    private static String normalizedUsername(String raw) {
        String normalized = SemanticSnapshot.normalize(raw);
        while (normalized.startsWith("@")) normalized = normalized.substring(1).trim();
        return normalized;
    }

    private static long parseCount(String raw) throws SemanticException {
        String normalized = SemanticSnapshot.normalize(raw).toUpperCase(Locale.ROOT)
                .replace(",", "").replace(" ", "");
        long multiplier = 1L;
        if (normalized.endsWith("K")) {
            multiplier = 1_000L;
            normalized = normalized.substring(0, normalized.length() - 1);
        } else if (normalized.endsWith("M")) {
            multiplier = 1_000_000L;
            normalized = normalized.substring(0, normalized.length() - 1);
        } else if (normalized.endsWith("B")) {
            multiplier = 1_000_000_000L;
            normalized = normalized.substring(0, normalized.length() - 1);
        } else if (normalized.endsWith("万")) {
            multiplier = 10_000L;
            normalized = normalized.substring(0, normalized.length() - 1);
        } else if (normalized.endsWith("亿")) {
            multiplier = 100_000_000L;
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        try {
            BigDecimal value = new BigDecimal(normalized).multiply(BigDecimal.valueOf(multiplier));
            if (value.signum() < 0 || value.compareTo(BigDecimal.valueOf(Long.MAX_VALUE)) > 0) {
                throw new NumberFormatException();
            }
            return value.setScale(0, RoundingMode.DOWN).longValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new SemanticException("invalid_metric");
        }
    }
}
