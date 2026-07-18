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
