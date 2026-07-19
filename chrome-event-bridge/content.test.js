const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const followerCore = require("./core.js");
const bindingCore = require("./binding-core.js");
const optionsCore = require("./options-core.js");

test("activity content script reports account binding health on alarm ticks", async () => {
  const listeners = [];
  const messages = [];
  const settings = {
    enabled: true,
    accountId: "account-02",
    deviceId: "phone-02",
    expectedTikTokUsername: "ikun.bags5",
    dashboardUrl: "http://127.0.0.1:8766",
    browserFollowbackEnabled: false,
  };
  const accountLink = {
    href: "https://www.tiktok.com/@ikun.bags5",
    getClientRects() { return [{}]; },
    getAttribute(name) { return name === "href" ? this.href : null; },
  };
  const document = {
    body: { textContent: "" },
    documentElement: {},
    querySelector(selector) {
      return selector.includes("avatar") ? {} : null;
    },
    querySelectorAll(selector) {
      return selector.includes("nav a[href*='/@']") ? [accountLink] : [];
    },
  };
  const chrome = {
    runtime: {
      lastError: null,
      onMessage: { addListener(listener) { listeners.push(listener); } },
      sendMessage(message, callback) {
        messages.push(message);
        callback({ ok: true, result: { recorded: true } });
      },
    },
    storage: {
      local: {
        get(_keys, callback) { callback({ tikpocSettings: settings }); },
        set(_values, callback) { callback(); },
      },
      onChanged: { addListener() {} },
    },
  };
  class MutationObserver { observe() {} }
  class Element {}
  const location = { pathname: "/@ikun.bags5" };
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
  vm.runInNewContext(source, {
    chrome,
    document,
    Element,
    globalThis: {
      TikPocBindingCore: bindingCore,
      TikPocFollowerCore: followerCore,
      TikPocOptionsCore: optionsCore,
      crypto: { randomUUID: () => "activity-tab" },
      location,
    },
    location,
    MutationObserver,
    setTimeout: () => 1,
    clearTimeout() {},
  });

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(messages.length, 1);
  assert.equal(listeners.length, 1);
  listeners[0]({ type: "TIKPOC_HEALTH_TICK" });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(messages.length, 2);
  for (const { body, ...message } of messages) {
    assert.deepEqual({ ...message, body: { ...body, timestamp_ms: 0 } }, {
      type: "TIKPOC_BROWSER_HEALTH",
      dashboardUrl: settings.dashboardUrl,
      body: {
        account_id: "account-02",
        device_id: "phone-02",
        page_role: "activity",
        path: "/@ikun.bags5",
        signed_in: true,
        observed_username: "ikun.bags5",
        binding_state: "ready",
        timestamp_ms: 0,
      },
    });
    assert.equal(typeof body.timestamp_ms, "number");
  }
});

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
