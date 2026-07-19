const assert = require("node:assert/strict");
const test = require("node:test");

const core = require("./auto-connect-core.js");

function binding(overrides = {}) {
  return {
    account_id: "account-01",
    device_id: "phone-01",
    expected_tiktok_username: "shop_one",
    browser_profile_label: "TikPoc 01",
    enabled: true,
    binding_ready: true,
    ...overrides,
  };
}

test("resolves one normalized visible username to one ready server binding", () => {
  const result = core.resolveAutoBinding({
    observedUsername: "@SHOP_ONE",
    bindings: [binding()],
    settings: {},
  });

  assert.equal(result.state, "matched");
  assert.equal(result.binding.account_id, "account-01");
});

test("does not bind missing, disabled, or ambiguous server mappings", () => {
  assert.equal(core.resolveAutoBinding({
    observedUsername: "missing",
    bindings: [binding()],
    settings: {},
  }).state, "no_match");
  assert.equal(core.resolveAutoBinding({
    observedUsername: "shop_one",
    bindings: [binding({ enabled: false })],
    settings: {},
  }).state, "no_match");
  assert.equal(core.resolveAutoBinding({
    observedUsername: "shop_one",
    bindings: [binding(), binding({ account_id: "account-02", device_id: "phone-02" })],
    settings: {},
  }).state, "ambiguous");
});

test("manual binding mode suppresses automatic matching", () => {
  const result = core.resolveAutoBinding({
    observedUsername: "shop_one",
    bindings: [binding()],
    settings: { bindingMode: "manual" },
  });

  assert.equal(result.state, "manual");
  assert.equal(result.binding, null);
});

test("same server-owned identity needs no storage update", () => {
  assert.equal(core.needsBindingUpdate({
    bindingMode: "auto",
    accountId: "account-01",
    deviceId: "phone-01",
    expectedTikTokUsername: "shop_one",
    browserProfileLabel: "TikPoc 01",
  }, binding()), false);
});

test("automatic rebind preserves runtime choices and resets only the old account", () => {
  const stored = {
    tikpocSettings: {
      accountId: "account-01",
      deviceId: "phone-01",
      expectedTikTokUsername: "shop_one",
      browserProfileLabel: "TikPoc 01",
      dashboardUrl: "http://127.0.0.1:8766",
      enabled: true,
      browserFollowbackEnabled: true,
      browserDmEnabled: false,
      autoOpenActivity: true,
    },
    tikpocFollowerBaselines: { "account-01": 1, "account-02": 2 },
    tikpocProcessedFollowers: {
      "follower:account-01:buyer:event": { status: "baseline" },
      "follower:account-02:buyer:event": { status: "completed" },
    },
    tikpocDmBaselines: { "account-01": {}, "account-02": {} },
    tikpocDmProcessed: {
      old: { accountId: "account-01" },
      keep: { accountId: "account-02" },
    },
  };

  const update = core.autoBindingStorageUpdate(
    stored,
    binding({
      account_id: "account-02",
      device_id: "phone-02",
      expected_tiktok_username: "shop_two",
      browser_profile_label: "TikPoc 02",
    }),
    "shop_two",
    0,
  );

  assert.deepEqual(update.tikpocSettings, {
    accountId: "account-02",
    deviceId: "phone-02",
    expectedTikTokUsername: "shop_two",
    browserProfileLabel: "TikPoc 02",
    dashboardUrl: "http://127.0.0.1:8766",
    enabled: true,
    browserFollowbackEnabled: true,
    browserDmEnabled: false,
    autoOpenActivity: true,
    bindingMode: "auto",
  });
  assert.deepEqual(update.tikpocFollowerBaselines, { "account-02": 2 });
  assert.deepEqual(Object.keys(update.tikpocProcessedFollowers), [
    "follower:account-02:buyer:event",
  ]);
  assert.deepEqual(update.tikpocDmBaselines, { "account-02": {} });
  assert.deepEqual(Object.keys(update.tikpocDmProcessed), ["keep"]);
  assert.deepEqual(update.tikpocBindingStatus, {
    accountId: "account-02",
    state: "ready",
    observedUsername: "shop_two",
    observedAt: 0,
    autoConnectState: "matched",
  });
});

test("fresh automatic binding keeps action switches off", () => {
  const update = core.autoBindingStorageUpdate(
    { tikpocSettings: {} },
    binding(),
    "shop_one",
  );

  assert.equal(update.tikpocSettings.enabled, true);
  assert.equal(update.tikpocSettings.browserFollowbackEnabled, false);
  assert.equal(update.tikpocSettings.browserDmEnabled, false);
  assert.equal(update.tikpocSettings.dashboardUrl, "http://127.0.0.1:8766");
});
