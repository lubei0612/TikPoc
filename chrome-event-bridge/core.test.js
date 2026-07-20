const test = require("node:test");
const assert = require("node:assert/strict");

const core = require("./core.js");

test("detects English and Chinese follower notifications", () => {
  assert.equal(core.isFollowerNotification("Alex followed you 2m"), true);
  assert.equal(core.isFollowerNotification("小明 关注了你 1分钟前"), true);
  assert.equal(core.isFollowerNotification("小明 开始关注你"), true);
  assert.equal(core.isFollowerNotification("Alex liked your video"), false);
});

test("extracts only TikTok profile usernames", () => {
  assert.deepEqual(
    core.parseTikTokProfileUrl("https://www.tiktok.com/@Some.User?lang=en"),
    {
      username: "Some.User",
      profileUrl: "https://www.tiktok.com/@Some.User",
    },
  );
  assert.equal(core.parseTikTokProfileUrl("https://example.com/@sample"), null);
  assert.equal(core.parseTikTokProfileUrl("https://www.tiktok.com/explore"), null);
});

test("classifies follow and completed button labels", () => {
  assert.equal(core.followButtonState("Follow back"), "actionable");
  assert.equal(core.followButtonState("关注"), "actionable");
  assert.equal(core.followButtonState("回关"), "actionable");
  assert.equal(core.followButtonState("Following"), "completed");
  assert.equal(core.followButtonState("已关注"), "completed");
  assert.equal(core.followButtonState("Message"), "other");
});

test("builds stable account-scoped follower keys", () => {
  assert.equal(
    core.buildFollowerDedupKey("Account-01", "Some.User"),
    "follower:account-01:some.user",
  );
  assert.equal(
    core.buildFollowerDedupKey("Account-01", "Some.User", " Activity 42 "),
    "follower:account-01:some.user:activity 42",
  );
});

test("prefers stable follower event attributes over visible timestamps", () => {
  const nestedIdentity = {
    getAttribute(name) {
      return name === "data-event-id" ? "nested-event-17" : "";
    },
  };
  const timestamp = {
    getAttribute(name) {
      return name === "datetime" ? "2026-07-18T09:00:00Z" : "";
    },
    textContent: "2m",
  };
  const row = {
    getAttribute(name) {
      return name === "data-notification-id" ? "notification-42" : "";
    },
    querySelector(selector) {
      return selector.includes("data-event-id") ? nestedIdentity : timestamp;
    },
  };
  assert.equal(core.extractFollowerEventId(row), "notification-42");

  row.getAttribute = () => "";
  assert.equal(core.extractFollowerEventId(row), "nested-event-17");

  nestedIdentity.getAttribute = () => "";
  assert.equal(core.extractFollowerEventId(row), "2026-07-18T09:00:00Z");
});

test("gates browser features with master and independent switches", () => {
  assert.equal(core.browserFeatureEnabled({ enabled: true }, "browserFollowbackEnabled"), true);
  assert.equal(
    core.browserFeatureEnabled(
      { enabled: true, browserFollowbackEnabled: false, browserDmEnabled: true },
      "browserFollowbackEnabled",
    ),
    false,
  );
  assert.equal(
    core.browserFeatureEnabled(
      { enabled: true, browserFollowbackEnabled: false, browserDmEnabled: true },
      "browserDmEnabled",
    ),
    true,
  );
  assert.equal(
    core.browserFeatureEnabled({ enabled: false, browserDmEnabled: true }, "browserDmEnabled"),
    false,
  );
});

test("rejects rows that lack an explicit follower phrase", () => {
  assert.equal(
    core.classifyCandidate({
      rowText: "Suggested account",
      profileUrl: "https://www.tiktok.com/@sample",
      buttonLabels: ["Follow"],
    }),
    null,
  );
});

test("classifies an unambiguous follower row", () => {
  assert.deepEqual(
    core.classifyCandidate({
      rowText: "Sample followed you 3m",
      profileUrl: "https://www.tiktok.com/@sample",
      buttonLabels: ["Message", "Follow back"],
    }),
    {
      username: "sample",
      profileUrl: "https://www.tiktok.com/@sample",
      buttonIndex: 1,
      state: "actionable",
    },
  );
});

test("never retries baseline or completed follower records", () => {
  assert.equal(
    core.shouldAttemptRecord({ status: "baseline", attempts: 0, updatedAt: 0 }, 1000),
    false,
  );
  assert.equal(
    core.shouldAttemptRecord({ status: "completed", attempts: 1, updatedAt: 0 }, 1000),
    false,
  );
});

test("retries unresolved records only after the delay and below the cap", () => {
  const record = { status: "unresolved", attempts: 1, updatedAt: 1000 };
  assert.equal(core.shouldAttemptRecord(record, 1500, 1000, 2), false);
  assert.equal(core.shouldAttemptRecord(record, 2000, 1000, 2), true);
  assert.equal(
    core.shouldAttemptRecord({ ...record, attempts: 2 }, 5000, 1000, 2),
    false,
  );
});

test("coalesces activity scans to one active run and one pending rerun", async () => {
  assert.equal(typeof core.createCoalescingRunner, "function");
  let release;
  let active = 0;
  let maxActive = 0;
  let runs = 0;
  const firstRun = new Promise((resolve) => { release = resolve; });
  const request = core.createCoalescingRunner(async () => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    runs += 1;
    if (runs === 1) {
      await firstRun;
    }
    active -= 1;
  });

  const first = request();
  const second = request();
  request();
  release();
  await Promise.all([first, second]);

  assert.equal(runs, 2);
  assert.equal(maxActive, 1);
});

test("installs continuous activity triggers", () => {
  assert.equal(typeof core.installContinuousTriggers, "function");
  const documentEvents = [];
  const windowEvents = [];
  const schedule = () => {};

  core.installContinuousTriggers(
    { addEventListener(name, handler) { documentEvents.push([name, handler]); } },
    { addEventListener(name, handler) { windowEvents.push([name, handler]); } },
    schedule,
  );

  assert.deepEqual(documentEvents, [["visibilitychange", schedule]]);
  assert.deepEqual(windowEvents, [
    ["pageshow", schedule],
    ["popstate", schedule],
    ["hashchange", schedule],
  ]);
});

test("installs a fifteen second activity watchdog", () => {
  assert.equal(typeof core.installWatchdog, "function");
  const calls = [];
  const schedule = () => {};

  core.installWatchdog((handler, intervalMs) => {
    calls.push([handler, intervalMs]);
    return 17;
  }, schedule);

  assert.deepEqual(calls, [[schedule, 15_000]]);
});

test("reopens Activity only after the bounded interval", () => {
  assert.equal(typeof core.shouldOpenActivity, "function");
  assert.equal(core.shouldOpenActivity(true, false, 1_000, 15_999), false);
  assert.equal(core.shouldOpenActivity(true, false, 1_000, 16_000), true);
  assert.equal(core.shouldOpenActivity(true, true, 1_000, 20_000), false);
  assert.equal(core.shouldOpenActivity(false, false, 0, 20_000), false);
});

test("only the current follower baseline version is actionable", () => {
  assert.equal(core.followerBaselineReady(1_000), false);
  assert.equal(core.followerBaselineReady({ version: 1 }), false);
  assert.equal(core.followerBaselineReady({ version: 2 }), true);
});

test("follower baseline waits for rows or a stable empty panel", () => {
  assert.equal(core.shouldEstablishFollowerBaseline({
    candidateCount: 4,
    activityOpenedAt: 1_000,
    now: 1_500,
  }), true);
  assert.equal(core.shouldEstablishFollowerBaseline({
    candidateCount: 0,
    activityOpenedAt: 1_000,
    now: 20_000,
  }), false);
  assert.equal(core.shouldEstablishFollowerBaseline({
    candidateCount: 0,
    activityOpenedAt: 1_000,
    now: 31_000,
  }), true);
  assert.equal(core.shouldEstablishFollowerBaseline({
    candidateCount: 0,
    activityOpenedAt: 0,
    now: 60_000,
  }), false);
});

test("disabled followback still establishes a baseline before staying read-only", () => {
  assert.equal(core.followerScanPhase({
    baselineReady: false,
    followbackEnabled: false,
  }), "baseline");
  assert.equal(core.followerScanPhase({
    baselineReady: true,
    followbackEnabled: false,
  }), "observe");
  assert.equal(core.followerScanPhase({
    baselineReady: true,
    followbackEnabled: true,
  }), "action");
});
