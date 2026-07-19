const assert = require("node:assert/strict");
const test = require("node:test");

const autoConnect = require("./auto-connect.js");

function visibleDocument(username = "ikun.bags5") {
  const link = {
    href: `https://www.tiktok.com/@${username}`,
    getClientRects() { return [{}]; },
    getAttribute(name) { return name === "href" ? this.href : null; },
  };
  return {
    body: { textContent: "" },
    querySelectorAll() { return [link]; },
  };
}

function memoryStorage(initial = {}) {
  const values = structuredClone(initial);
  return {
    values,
    async get() { return structuredClone(values); },
    async set(update) { Object.assign(values, structuredClone(update)); },
  };
}

function serverBinding(overrides = {}) {
  return {
    account_id: "account-02",
    device_id: "phone-02",
    expected_tiktok_username: "ikun.bags5",
    browser_profile_label: "Your Chrome",
    enabled: true,
    binding_ready: true,
    ...overrides,
  };
}

test("connects one visible account through the redacted binding endpoint", async () => {
  const storage = memoryStorage({ tikpocSettings: {} });
  const calls = [];
  const connector = autoConnect.createAutoConnector({
    documentValue: visibleDocument(),
    storage,
    now: () => 100,
    async getBindings(dashboardUrl) {
      calls.push(dashboardUrl);
      return [serverBinding()];
    },
  });

  assert.equal(await connector.connect(), "matched");
  assert.deepEqual(calls, ["http://127.0.0.1:8766"]);
  assert.deepEqual(storage.values.tikpocSettings, {
    accountId: "account-02",
    deviceId: "phone-02",
    expectedTikTokUsername: "ikun.bags5",
    browserProfileLabel: "Your Chrome",
    dashboardUrl: "http://127.0.0.1:8766",
    bindingMode: "auto",
    enabled: true,
    browserFollowbackEnabled: false,
    browserDmEnabled: false,
    autoOpenActivity: false,
  });
  assert.equal(storage.values.tikpocAutoConnectStatus.state, "matched");
});

test("does not write an account mapping for blocked visible identity states", async () => {
  for (const [text, expected] of [
    ["Log in", "signed_out"],
    ["Security check - verify to continue", "verification_required"],
  ]) {
    const storage = memoryStorage({ tikpocSettings: {} });
    const connector = autoConnect.createAutoConnector({
      documentValue: { body: { textContent: text }, querySelectorAll() { return []; } },
      storage,
      async getBindings() { throw new Error("must not fetch"); },
    });

    assert.equal(await connector.connect(), expected);
    assert.equal(storage.values.tikpocSettings.accountId, undefined);
    assert.equal(storage.values.tikpocAutoConnectStatus.state, expected);
  }
});

test("records no-match and ambiguity without replacing the current account", async () => {
  for (const [bindings, expected] of [
    [[], "no_match"],
    [[serverBinding(), serverBinding({ account_id: "duplicate", device_id: "other" })], "ambiguous"],
  ]) {
    const storage = memoryStorage({
      tikpocSettings: { accountId: "account-01", bindingMode: "auto" },
    });
    const connector = autoConnect.createAutoConnector({
      documentValue: visibleDocument(),
      storage,
      async getBindings() { return bindings; },
    });

    assert.equal(await connector.connect(), expected);
    assert.equal(storage.values.tikpocSettings.accountId, "account-01");
    assert.equal(storage.values.tikpocAutoConnectStatus.state, expected);
  }
});

test("manual mode skips visible identity and server discovery", async () => {
  const storage = memoryStorage({
    tikpocSettings: { accountId: "account-01", bindingMode: "manual" },
  });
  const connector = autoConnect.createAutoConnector({
    documentValue: visibleDocument(),
    storage,
    async getBindings() { throw new Error("must not fetch"); },
  });

  assert.equal(await connector.connect(), "manual");
  assert.equal(storage.values.tikpocSettings.accountId, "account-01");
});
