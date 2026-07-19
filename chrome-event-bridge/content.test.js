const assert = require("node:assert/strict");
const test = require("node:test");

const followerCore = require("./core.js");

test("two accounts isolate the same follower notification identity", () => {
  const first = followerCore.buildFollowerDedupKey(
    "account-01",
    "same.follower",
    "same-event",
  );
  const second = followerCore.buildFollowerDedupKey(
    "account-02",
    "same.follower",
    "same-event",
  );

  assert.notEqual(first, second);
  assert.deepEqual(new Set([first, second]).size, 2);
});
