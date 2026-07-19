const assert = require("node:assert/strict");
const test = require("node:test");

const optionsCore = require("./options-core.js");

test("selected binding replaces all server-owned identity fields together", () => {
  const settings = optionsCore.settingsForBinding(
    { dashboardUrl: "http://127.0.0.1:8766", enabled: true },
    {
      account_id: "account-02",
      device_id: "phone-02",
      expected_tiktok_username: "shop_two",
      browser_profile_label: "客服二号",
    },
  );

  assert.deepEqual(settings, {
    dashboardUrl: "http://127.0.0.1:8766",
    enabled: true,
    accountId: "account-02",
    deviceId: "phone-02",
    expectedTikTokUsername: "shop_two",
    browserProfileLabel: "客服二号",
  });
});

test("rebind reset deletes only records owned by the old account", () => {
  const reset = optionsCore.resetForAccount({
    tikpocFollowerBaselines: {
      "account-01": 100,
      "account-02": 200,
    },
    tikpocProcessedFollowers: {
      "follower:account-01:buyer:a": { status: "completed" },
      "follower:account-010:buyer:b": { status: "completed" },
      "follower:account-02:buyer:c": { status: "completed" },
    },
    tikpocDmBaselines: {
      "account-01": { thread: "old" },
      "account-02": { thread: "keep" },
    },
    tikpocDmProcessed: {
      old: { accountId: "account-01", state: "sent" },
      other: { accountId: "account-02", state: "sent" },
      legacy: { state: "sent" },
    },
  }, "ACCOUNT-01");

  assert.deepEqual(reset.tikpocFollowerBaselines, { "account-02": 200 });
  assert.deepEqual(reset.tikpocProcessedFollowers, {
    "follower:account-010:buyer:b": { status: "completed" },
    "follower:account-02:buyer:c": { status: "completed" },
  });
  assert.deepEqual(reset.tikpocDmBaselines, {
    "account-02": { thread: "keep" },
  });
  assert.deepEqual(reset.tikpocDmProcessed, {
    other: { accountId: "account-02", state: "sent" },
    legacy: { state: "sent" },
  });
});

test("popup binding status is ignored after the profile is rebound", () => {
  assert.deepEqual(
    optionsCore.popupBinding(
      { accountId: "account-02", expectedTikTokUsername: "shop_two" },
      { accountId: "account-01", observedUsername: "shop_one", state: "ready" },
    ),
    { state: "unverified", observedUsername: "" },
  );
});

test("only a change from an existing account requires rebind confirmation", () => {
  assert.equal(optionsCore.requiresRebindConfirmation("", "account-01"), false);
  assert.equal(optionsCore.requiresRebindConfirmation("account-01", "ACCOUNT-01"), false);
  assert.equal(optionsCore.requiresRebindConfirmation("account-01", "account-02"), true);
});

test("popup localizes every visible identity state", () => {
  assert.equal(optionsCore.bindingStateLabel("ready"), "已就绪");
  assert.equal(optionsCore.bindingStateLabel("mismatch"), "身份不符");
  assert.equal(optionsCore.bindingStateLabel("signed_out"), "已退出");
  assert.equal(optionsCore.bindingStateLabel("verification_required"), "需验证");
  assert.equal(optionsCore.bindingStateLabel("unverified"), "未验证");
  assert.equal(optionsCore.bindingStateLabel("other"), "未验证");
});

test("content scripts persist a minimal account-scoped binding observation", () => {
  assert.deepEqual(
    optionsCore.bindingObservation(
      "account-01",
      { state: "mismatch", observedUsername: "shop_two" },
      1234,
    ),
    {
      accountId: "account-01",
      state: "mismatch",
      observedUsername: "shop_two",
      observedAt: 1234,
    },
  );
});

test("runtime setting updates preserve the complete account binding", () => {
  assert.deepEqual(
    optionsCore.mergeRuntimeSettings(
      {
        accountId: "account-01",
        deviceId: "phone-01",
        expectedTikTokUsername: "shop_one",
        browserProfileLabel: "客服一号",
        enabled: false,
      },
      { enabled: true, dashboardUrl: "http://127.0.0.1:8766" },
    ),
    {
      accountId: "account-01",
      deviceId: "phone-01",
      expectedTikTokUsername: "shop_one",
      browserProfileLabel: "客服一号",
      enabled: true,
      dashboardUrl: "http://127.0.0.1:8766",
    },
  );
});

test("visible identity remains observable while action switches are off", () => {
  assert.equal(optionsCore.canObserveBinding({ accountId: "account-01" }), true);
  assert.equal(optionsCore.canObserveBinding({ accountId: "" }), false);
  assert.equal(optionsCore.canObserveBinding(null), false);
});

test("binding observation writes do not reschedule their own scanner", () => {
  assert.equal(optionsCore.shouldScheduleForStorageChanges({
    tikpocBindingStatus: { oldValue: null, newValue: { state: "ready" } },
  }), false);
  assert.equal(optionsCore.shouldScheduleForStorageChanges({
    tikpocSettings: { oldValue: {}, newValue: { enabled: true } },
  }), true);
  assert.equal(optionsCore.shouldScheduleForStorageChanges({}), false);
});

test("automatic binding is the default and manual mode is explicit", () => {
  assert.equal(optionsCore.bindingMode({}), "auto");
  assert.equal(optionsCore.bindingMode({ bindingMode: "auto" }), "auto");
  assert.equal(optionsCore.bindingMode({ bindingMode: "manual" }), "manual");
  assert.equal(optionsCore.bindingModeLabel({}), "自动识别");
  assert.equal(optionsCore.bindingModeLabel({ bindingMode: "manual" }), "人工绑定");
});
