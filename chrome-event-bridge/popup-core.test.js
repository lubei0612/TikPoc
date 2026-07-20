const assert = require("node:assert/strict");
const test = require("node:test");

const core = require("./popup-core.js");

test("monitoring button starts and stops a Profile-local observer", () => {
  assert.deepEqual(core.monitoringButton({ monitoringStarted: false }), {
    label: "开始监控",
    action: "start",
    disabled: false,
  });
  assert.deepEqual(core.monitoringButton({ monitoringStarted: true }), {
    label: "停止监控",
    action: "stop",
    disabled: false,
  });
});

test("monitoring button exposes pending state", () => {
  assert.deepEqual(core.monitoringButton({}, true), {
    label: "处理中...",
    action: "pending",
    disabled: true,
  });
});
