const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("browser event transport sends JSON without setting Origin", async () => {
  let messageListener;
  let request;
  const context = {
    URL,
    Set,
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            messageListener = listener;
          },
        },
        onInstalled: { addListener() {} },
        openOptionsPage() {},
      },
    },
    async fetch(url, options) {
      request = { url, options };
      return {
        ok: true,
        async json() {
          return { accepted: true };
        },
      };
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "background.js"), "utf8");
  vm.runInNewContext(source, context);

  const response = await new Promise((resolve) => {
    const asynchronous = messageListener(
      {
        type: "TIKPOC_REPORT_EVENT",
        dashboardUrl: "http://127.0.0.1:8766",
        event: { account_id: "account-01" },
      },
      {},
      resolve,
    );
    assert.equal(asynchronous, true);
  });

  assert.equal(response.ok, true);
  assert.equal(response.result.accepted, true);
  assert.equal(request.url, "http://127.0.0.1:8766/api/browser-events");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.equal(Object.hasOwn(request.options.headers, "Origin"), false);
});

test("browser DM, action, and health messages share the localhost JSON transport", async () => {
  let messageListener;
  const requests = [];
  const context = {
    URL,
    Set,
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) {
            messageListener = listener;
          },
        },
        onInstalled: { addListener() {} },
        onStartup: { addListener() {} },
        openOptionsPage() {},
      },
      alarms: { create() {}, onAlarm: { addListener() {} } },
      tabs: { query: async () => [], sendMessage: async () => {} },
    },
    async fetch(url, options) {
      requests.push({ url, options });
      return {
        ok: true,
        async json() {
          return { accepted: true };
        },
      };
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "background.js"), "utf8");
  vm.runInNewContext(source, context);

  const routes = [
    ["TIKPOC_DM_PLAN", "/api/browser-dm/reply-plan"],
    ["TIKPOC_DM_RESULT", "/api/browser-dm/reply-result"],
    ["TIKPOC_WELCOME_PLAN", "/api/browser-dm/welcome-plan"],
    ["TIKPOC_WELCOME_RESULT", "/api/browser-dm/welcome-result"],
    ["TIKPOC_ACTION_CLAIM", "/api/browser-actions/claim"],
    ["TIKPOC_ACTION_RESULT", "/api/browser-actions/result"],
    ["TIKPOC_BROWSER_HEALTH", "/api/browser-health"],
  ];
  for (const [type] of routes) {
    const response = await new Promise((resolve) => {
      const asynchronous = messageListener(
        {
          type,
          dashboardUrl: "http://localhost:8766/base-path",
          body: { account_id: "account-01", marker: type },
        },
        {},
        resolve,
      );
      assert.equal(asynchronous, true);
    });
    assert.equal(response.ok, true);
  }

  assert.deepEqual(
    requests.map((request) => request.url),
    routes.map(([, route]) => `http://localhost:8766${route}`),
  );
  for (let index = 0; index < requests.length; index += 1) {
    const request = requests[index];
    assert.equal(request.options.method, "POST");
    assert.deepEqual(JSON.parse(request.options.body), {
      account_id: "account-01",
      marker: routes[index][0],
    });
    assert.equal(Object.hasOwn(request.options.headers, "Origin"), false);
  }
});

test("browser transports reject non-local dashboard origins", async () => {
  let messageListener;
  let fetched = false;
  const context = {
    URL,
    Set,
    chrome: {
      runtime: {
        onMessage: { addListener(listener) { messageListener = listener; } },
        onInstalled: { addListener() {} },
        onStartup: { addListener() {} },
        openOptionsPage() {},
      },
      alarms: { create() {}, onAlarm: { addListener() {} } },
      tabs: { query: async () => [], sendMessage: async () => {} },
    },
    async fetch() {
      fetched = true;
      throw new Error("unexpected fetch");
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "background.js"), "utf8"),
    context,
  );

  const response = await new Promise((resolve) => {
    assert.equal(
      messageListener(
        {
          type: "TIKPOC_DM_PLAN",
          dashboardUrl: "https://example.com",
          body: {},
        },
        {},
        resolve,
      ),
      true,
    );
  });
  assert.equal(response.ok, false);
  assert.equal(fetched, false);
});

test("browser binding transport reads only the loopback binding endpoint", async () => {
  let messageListener;
  let request;
  const context = {
    URL,
    Set,
    chrome: {
      runtime: {
        onMessage: { addListener(listener) { messageListener = listener; } },
        onInstalled: { addListener() {} },
        onStartup: { addListener() {} },
        openOptionsPage() {},
      },
      alarms: { create() {}, onAlarm: { addListener() {} } },
      tabs: { query: async () => [], sendMessage: async () => {} },
    },
    async fetch(url, options) {
      request = { url, options };
      return {
        ok: true,
        async json() { return { accounts: [{ account_id: "account-01" }] }; },
      };
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "background.js"), "utf8"),
    context,
  );

  const response = await new Promise((resolve) => {
    assert.equal(messageListener({
      type: "TIKPOC_GET_BINDINGS",
      dashboardUrl: "http://127.0.0.1:8766/path",
    }, {}, resolve), true);
  });

  assert.equal(response.ok, true);
  assert.equal(response.result.accounts[0].account_id, "account-01");
  assert.equal(request.url, "http://127.0.0.1:8766/api/browser-bindings");
  assert.equal(request.options.method, "GET");
  assert.equal(Object.hasOwn(request.options, "body"), false);

  request = null;
  const rejected = await new Promise((resolve) => {
    assert.equal(messageListener({
      type: "TIKPOC_GET_BINDINGS",
      dashboardUrl: "https://example.com",
    }, {}, resolve), true);
  });
  assert.equal(rejected.ok, false);
  assert.equal(request, null);
});

test("a completed followback wakes open TikTok tabs for welcome scanning", async () => {
  let messageListener;
  const notifications = [];
  const context = {
    URL,
    Set,
    chrome: {
      runtime: {
        onMessage: { addListener(listener) { messageListener = listener; } },
        onInstalled: { addListener() {} },
        onStartup: { addListener() {} },
        openOptionsPage() {},
      },
      alarms: { create() {}, onAlarm: { addListener() {} } },
      tabs: {
        async query() { return [{ id: 11 }, { id: 12 }]; },
        async sendMessage(tabId, message) { notifications.push({ tabId, message }); },
      },
    },
    async fetch() {
      return { ok: true, async json() { return { recorded: true }; } };
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "background.js"), "utf8"),
    context,
  );

  const response = await new Promise((resolve) => {
    assert.equal(messageListener({
      type: "TIKPOC_ACTION_RESULT",
      dashboardUrl: "http://127.0.0.1:8766",
      body: { action_type: "followback", state: "completed" },
    }, {}, resolve), true);
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(response.ok, true);
  assert.deepEqual(JSON.parse(JSON.stringify(notifications)), [
    { tabId: 11, message: { type: "TIKPOC_HEALTH_TICK" } },
    { tabId: 12, message: { type: "TIKPOC_HEALTH_TICK" } },
  ]);
});
