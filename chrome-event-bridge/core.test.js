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
