(function exposeFollowerCore(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.TikPocFollowerCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createCore() {
  const FOLLOWER_PATTERNS = [
    /\bfollowed you\b/i,
    /\bstarted following you\b/i,
    /\bis following you\b/i,
    /关注了你/,
    /开始关注你/,
    /關注了你/,
    /開始關注你/,
    /追蹤了你/,
    /開始追蹤你/,
  ];

  const ACTIONABLE_LABELS = new Set([
    "follow",
    "follow back",
    "关注",
    "關注",
    "回关",
    "回關",
  ]);
  const COMPLETED_LABELS = new Set([
    "following",
    "friends",
    "已关注",
    "已關注",
    "好友",
    "朋友",
  ]);

  function normalizeText(value) {
    return String(value || "")
      .normalize("NFKC")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isFollowerNotification(value) {
    const text = normalizeText(value);
    return text.length > 0 && FOLLOWER_PATTERNS.some((pattern) => pattern.test(text));
  }

  function parseTikTokProfileUrl(value) {
    let url;
    try {
      url = new URL(String(value || ""));
    } catch (_error) {
      return null;
    }
    if (!new Set(["tiktok.com", "www.tiktok.com"]).has(url.hostname.toLowerCase())) {
      return null;
    }
    const match = url.pathname.match(/^\/@([^/?#]+)/);
    if (!match) {
      return null;
    }
    let username;
    try {
      username = decodeURIComponent(match[1]);
    } catch (_error) {
      return null;
    }
    if (!/^[A-Za-z0-9._]+$/.test(username)) {
      return null;
    }
    return {
      username,
      profileUrl: `https://www.tiktok.com/@${username}`,
    };
  }

  function followButtonState(value) {
    const label = normalizeText(value).toLowerCase();
    if (ACTIONABLE_LABELS.has(label)) {
      return "actionable";
    }
    if (COMPLETED_LABELS.has(label)) {
      return "completed";
    }
    return "other";
  }

  function buildFollowerDedupKey(accountId, username) {
    return `follower:${normalizeText(accountId).toLowerCase()}:${normalizeText(username).toLowerCase()}`;
  }

  function shouldAttemptRecord(
    record,
    now = Date.now(),
    retryAfterMs = 30 * 60 * 1000,
    maxAttempts = 2,
  ) {
    if (!record) {
      return true;
    }
    if (new Set(["baseline", "completed"]).has(record.status)) {
      return false;
    }
    if (Number(record.attempts || 0) >= Math.max(1, Number(maxAttempts || 1))) {
      return false;
    }
    return Number(now) - Number(record.updatedAt || 0) >= Math.max(0, Number(retryAfterMs || 0));
  }

  function classifyCandidate({ rowText, profileUrl, buttonLabels }) {
    if (!isFollowerNotification(rowText)) {
      return null;
    }
    const profile = parseTikTokProfileUrl(profileUrl);
    if (!profile || !Array.isArray(buttonLabels)) {
      return null;
    }
    const states = buttonLabels.map(followButtonState);
    let buttonIndex = states.indexOf("actionable");
    let state = "actionable";
    if (buttonIndex < 0) {
      buttonIndex = states.indexOf("completed");
      state = "completed";
    }
    if (buttonIndex < 0) {
      return {
        ...profile,
        buttonIndex: -1,
        state: "unresolved",
      };
    }
    return {
      ...profile,
      buttonIndex,
      state,
    };
  }

  return {
    buildFollowerDedupKey,
    classifyCandidate,
    followButtonState,
    isFollowerNotification,
    normalizeText,
    parseTikTokProfileUrl,
    shouldAttemptRecord,
  };
});
