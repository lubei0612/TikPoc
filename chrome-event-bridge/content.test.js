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
  const scheduled = [];
  const documentEvents = [];
  const windowEvents = [];
  const intervals = [];
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
    addEventListener(name, handler) { documentEvents.push([name, handler]); },
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
      addEventListener(name, handler) { windowEvents.push([name, handler]); },
    },
    location,
    MutationObserver,
    setTimeout: (callback) => { scheduled.push(callback); return scheduled.length; },
    setInterval: (callback, intervalMs) => {
      intervals.push([callback, intervalMs]);
      return intervals.length;
    },
    clearTimeout() {},
  });

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(messages.length, 1);
  assert.equal(listeners.length, 1);
  scheduled.length = 0;
  listeners[0]({ type: "TIKPOC_HEALTH_TICK" });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(messages.length, 2);
  assert.equal(scheduled.length, 1);
  assert.deepEqual(documentEvents.map(([name]) => name), ["visibilitychange"]);
  assert.deepEqual(windowEvents.map(([name]) => name), [
    "pageshow",
    "popstate",
    "hashchange",
  ]);
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0][1], 15_000);
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
        last_scan_at_ms: 0,
        last_success_at_ms: 0,
        scan_state: "not_started",
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

test("followback unresolved reason is included in the claimed action result", async () => {
  const messages = [];
  const scheduled = [];
  const settings = {
    enabled: true,
    accountId: "account-01",
    deviceId: "phone-01",
    expectedTikTokUsername: "ikun.bags4",
    dashboardUrl: "http://127.0.0.1:8766",
    browserFollowbackEnabled: true,
  };
  class Element {
    getClientRects() { return [{}]; }
  }
  const followButton = new Element();
  followButton.textContent = "Follow back";
  followButton.isConnected = true;
  followButton.getAttribute = () => null;
  followButton.click = () => {};
  const row = new Element();
  row.textContent = "ikun.bags6 started following you. Follow back";
  row.parentElement = null;
  row.getAttribute = (name) => name === "data-event-id" ? "controlled-event" : null;
  row.querySelector = () => null;
  row.querySelectorAll = () => [followButton];
  const followerLink = new Element();
  followerLink.href = "https://www.tiktok.com/@ikun.bags6";
  followerLink.parentElement = row;
  followerLink.getAttribute = (name) => name === "href" ? followerLink.href : null;
  const accountLink = new Element();
  accountLink.href = "https://www.tiktok.com/@ikun.bags4";
  accountLink.parentElement = null;
  accountLink.getAttribute = (name) => name === "href" ? accountLink.href : null;
  const storage = {
    tikpocSettings: settings,
    tikpocProcessedFollowers: {},
    tikpocFollowerBaselines: { "account-01": { version: 2, establishedAt: 1 } },
  };
  const document = {
    body: { textContent: "" },
    documentElement: {},
    addEventListener() {},
    querySelector(selector) {
      return selector.includes("avatar") ? {} : null;
    },
    querySelectorAll(selector) {
      if (selector.includes("nav a[href*='/@']")) return [accountLink];
      if (selector === "a[href*='/@']") return [followerLink, accountLink];
      return [];
    },
  };
  const chrome = {
    runtime: {
      lastError: null,
      onMessage: { addListener() {} },
      sendMessage(message, callback) {
        messages.push(message);
        const result = message.type === "TIKPOC_ACTION_CLAIM"
          ? { claimed: true }
          : { recorded: true, accepted: true };
        callback({ ok: true, result });
      },
    },
    storage: {
      local: {
        get(_keys, callback) { callback(storage); },
        set(values, callback) { Object.assign(storage, values); callback(); },
      },
      onChanged: { addListener() {} },
    },
  };
  class MutationObserver { observe() {} }
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
      location: { pathname: "/" },
      addEventListener() {},
    },
    location: { pathname: "/" },
    MutationObserver,
    setTimeout(callback, delayMs) {
      if (delayMs === 1800) {
        Promise.resolve().then(callback);
      } else {
        scheduled.push(callback);
      }
      return scheduled.length;
    },
    setInterval() { return 1; },
    clearTimeout() {},
  });

  await new Promise((resolve) => setImmediate(resolve));
  scheduled.shift()();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const actionResult = messages.find(
    (message) => message.type === "TIKPOC_ACTION_RESULT",
  );
  assert.equal(actionResult.body.state, "uncertain");
  assert.equal(actionResult.body.reason, "followback_unresolved");
});
