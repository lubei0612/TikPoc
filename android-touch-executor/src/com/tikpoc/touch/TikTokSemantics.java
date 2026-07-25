package com.tikpoc.touch;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Collections;
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
        SemanticSnapshot.Node usernameNode = uniqueByResource(snapshot, "username");
        SemanticSnapshot.Node followingNode = uniqueByResource(snapshot, "following_count");
        SemanticSnapshot.Node followersNode = uniqueByResource(snapshot, "followers_count");
        if (usernameNode == null || followingNode == null || followersNode == null) {
            throw new SemanticException("incomplete_profile_evidence");
        }
        String username = normalizedUsername(usernameNode.text);
        if (username.isEmpty()) throw new SemanticException("incomplete_profile_evidence");
        List<String> handles = new ArrayList<String>();
        for (SemanticSnapshot.Node node : snapshot.nodes) {
            if (node.visible && node.enabled && node.bounds.hasArea()
                    && node.resourceId.toLowerCase(Locale.ROOT).contains("video_item")) {
                handles.add("post:" + handles.size());
            }
        }
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
                    || node.searchableText().startsWith(normalizedAction + " "))) {
                matches.add(node);
            }
        }
        if (matches.isEmpty()) throw new SemanticException("missing_control");
        if (matches.size() != 1) throw new SemanticException("ambiguous_control");
        return matches.get(0);
    }

    public static String actionState(SemanticSnapshot.Node node) {
        if (node.selected) return "on";
        String searchable = node.searchableText();
        if (searchable.contains("selected") || searchable.contains("remove")) return "on";
        return "off";
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
