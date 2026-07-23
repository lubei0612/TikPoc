const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("injects the DM bridge into same-origin Messages frames only", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(__dirname, "manifest.json"), "utf8"),
  );
  const dmEntry = manifest.content_scripts.find((entry) =>
    entry.js.includes("dm-content.js"),
  );

  assert.deepEqual(dmEntry.matches, [
    "https://www.tiktok.com/messages*",
    "https://www.tiktok.com/business-suite/messages*",
  ]);
  assert.equal(dmEntry.all_frames, true);
  assert.equal(dmEntry.js.includes("content.js"), false);

  const activityEntry = manifest.content_scripts.find((entry) =>
    entry.js.includes("content.js"),
  );
  assert.deepEqual(activityEntry.exclude_matches, [
    "https://www.tiktok.com/messages*",
    "https://www.tiktok.com/business-suite/*",
  ]);
});

test("grants debugger permission for trusted message input", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(__dirname, "manifest.json"), "utf8"),
  );

  assert.ok(manifest.permissions.includes("debugger"));
});
