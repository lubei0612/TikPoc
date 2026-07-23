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

  function buildFollowerDedupKey(accountId, username, eventId = "") {
    const base = `follower:${normalizeText(accountId).toLowerCase()}:${normalizeText(username).toLowerCase()}`;
    const normalizedEventId = normalizeText(eventId).toLowerCase();
    return normalizedEventId ? `${base}:${normalizedEventId}` : base;
  }

  function extractFollowerEventId(row) {
    const stableAttributes = [
      "data-event-id",
      "data-notification-id",
      "data-activity-id",
      "data-id",
    ];
    for (const name of stableAttributes) {
      const value = row && typeof row.getAttribute === "function"
        ? normalizeText(row.getAttribute(name))
        : "";
      if (value) {
        return value;
      }
    }
    const nestedIdentity = row && typeof row.querySelector === "function"
      ? row.querySelector(stableAttributes.map((name) => `[${name}]`).join(", "))
      : null;
    for (const name of stableAttributes) {
      const value = nestedIdentity && typeof nestedIdentity.getAttribute === "function"
        ? normalizeText(nestedIdentity.getAttribute(name))
        : "";
      if (value) {
        return value;
      }
    }
    const timestamp = row && typeof row.querySelector === "function"
      ? row.querySelector("time, [data-timestamp], [data-time]")
      : null;
    for (const name of ["datetime", "data-timestamp", "data-time"]) {
      const value = timestamp && typeof timestamp.getAttribute === "function"
        ? normalizeText(timestamp.getAttribute(name))
        : "";
      if (value) {
        return value;
      }
    }
    return normalizeText(timestamp && timestamp.textContent);
  }

  function browserFeatureEnabled(settings, featureKey) {
    return Boolean(settings && settings.enabled && settings[featureKey] !== false);
  }

  function createCoalescingRunner(run) {
    let active = null;
    let requested = false;

    async function drain() {
      do {
        requested = false;
        await run();
      } while (requested);
    }

    return function request() {
      requested = true;
      if (!active) {
        active = drain().finally(() => {
          active = null;
        });
      }
      return active;
    };
  }

  function installContinuousTriggers(documentValue, windowValue, schedule) {
    if (documentValue && typeof documentValue.addEventListener === "function") {
      documentValue.addEventListener("visibilitychange", schedule);
    }
    if (windowValue && typeof windowValue.addEventListener === "function") {
      for (const eventName of ["pageshow", "popstate", "hashchange"]) {
        windowValue.addEventListener(eventName, schedule);
      }
    }
  }

  function installWatchdog(setIntervalValue, schedule) {
    return setIntervalValue(schedule, 15_000);
  }

  function shouldOpenActivity(
    autoOpen,
    panelVisible,
    lastOpenedAt,
    now = Date.now(),
  ) {
    return Boolean(
      autoOpen &&
      !panelVisible &&
      Number(now) - Number(lastOpenedAt || 0) >= 15_000
    );
  }

  function followerBaselineReady(value) {
    return Boolean(value && typeof value === "object" && value.version === 2);
  }

  function followerScanPhase({ baselineReady, followbackEnabled }) {
    if (!baselineReady) {
      return "baseline";
    }
    return followbackEnabled ? "action" : "observe";
  }

  function shouldEstablishFollowerBaseline({
    candidateCount,
    activityOpenedAt,
    now = Date.now(),
    emptyWaitMs = 30_000,
  }) {
    if (Number(candidateCount || 0) > 0) {
      return true;
    }
    const openedAt = Number(activityOpenedAt || 0);
    return openedAt > 0 && Number(now) - openedAt >= Number(emptyWaitMs);
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
    browserFeatureEnabled,
    buildFollowerDedupKey,
    classifyCandidate,
    createCoalescingRunner,
    extractFollowerEventId,
    followerBaselineReady,
    followerScanPhase,
    followButtonState,
    isFollowerNotification,
    installContinuousTriggers,
    installWatchdog,
    normalizeText,
    parseTikTokProfileUrl,
    shouldAttemptRecord,
    shouldEstablishFollowerBaseline,
    shouldOpenActivity,
  };
});
