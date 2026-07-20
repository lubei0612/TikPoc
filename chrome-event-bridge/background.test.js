const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function trustedSendHarness({ holdFirstAttach = false, failCommand = "" } = {}) {
  let messageListener;
  const attached = [];
  const detached = [];
  const commands = [];
  let releaseFirstAttach;
  let attachCount = 0;
  const firstAttach = new Promise((resolve) => { releaseFirstAttach = resolve; });
  const context = {
    URL,
    Set,
    Map,
    chrome: {
      runtime: {
        onMessage: { addListener(listener) { messageListener = listener; } },
        onInstalled: { addListener() {} },
        onStartup: { addListener() {} },
        openOptionsPage() {},
      },
      alarms: { create() {}, onAlarm: { addListener() {} } },
      tabs: { query: async () => [], sendMessage: async () => {} },
      debugger: {
        async attach(target) {
          attached.push({ ...target });
          attachCount += 1;
          if (holdFirstAttach && attachCount === 1) {
            await firstAttach;
          }
        },
        async sendCommand(target, method, params) {
          commands.push({ target: { ...target }, method, params });
          if (method === failCommand) {
            throw new Error("command failed");
          }
        },
        async detach(target) { detached.push({ ...target }); },
      },
    },
    async fetch() { throw new Error("unexpected fetch"); },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "background.js"), "utf8"),
    context,
  );
  function send(message, sender = {
    url: "https://www.tiktok.com/messages",
    tab: { id: 42, url: "https://www.tiktok.com/messages" },
  }) {
    return new Promise((resolve) => {
      assert.equal(messageListener(message, sender, resolve), true);
    });
  }
  return {
    attached,
    commands,
    detached,
    releaseFirstAttach,
    send,
  };
}

test("trusted message input attaches, types, submits, and detaches", async () => {
  const run = trustedSendHarness();

  const response = await run.send({
    type: "TIKPOC_TRUSTED_SEND",
    text: "Thanks for your interest.",
  });

  assert.equal(response.ok, true);
  assert.equal(response.result.submitted, true);
  assert.deepEqual(run.attached, [{ tabId: 42 }]);
  assert.deepEqual(run.commands.map(({ method }) => method), [
    "Input.dispatchKeyEvent",
    "Input.dispatchKeyEvent",
    "Input.dispatchKeyEvent",
    "Input.dispatchKeyEvent",
    "Input.insertText",
    "Input.dispatchKeyEvent",
    "Input.dispatchKeyEvent",
  ]);
  assert.equal(run.commands[4].params.text, "Thanks for your interest.");
  assert.deepEqual(run.detached, [{ tabId: 42 }]);
});

test("trusted message input rejects non-Messages senders", async () => {
  const run = trustedSendHarness();

  const response = await run.send(
    { type: "TIKPOC_TRUSTED_SEND", text: "Hello" },
    { url: "https://www.tiktok.com/@buyer", tab: { id: 42 } },
  );

  assert.equal(response.ok, false);
  assert.equal(run.attached.length, 0);
});

test("trusted message input serializes sends per tab", async () => {
  const run = trustedSendHarness({ holdFirstAttach: true });
  const first = run.send({ type: "TIKPOC_TRUSTED_SEND", text: "First" });
  const second = run.send({ type: "TIKPOC_TRUSTED_SEND", text: "Second" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(run.attached.length, 1);

  run.releaseFirstAttach();
  const responses = await Promise.all([first, second]);

  assert.equal(responses.every(({ ok }) => ok), true);
  assert.equal(run.attached.length, 2);
  assert.equal(run.detached.length, 2);
});

test("trusted message input detaches after a command failure", async () => {
  const run = trustedSendHarness({ failCommand: "Input.insertText" });

  const response = await run.send({
    type: "TIKPOC_TRUSTED_SEND",
    text: "Hello",
  });

  assert.equal(response.ok, false);
  assert.deepEqual(run.detached, [{ tabId: 42 }]);
});

test("trusted message input keeps separate tabs independent", async () => {
  const run = trustedSendHarness({ holdFirstAttach: true });
  const first = run.send({ type: "TIKPOC_TRUSTED_SEND", text: "First" });
  const second = run.send(
    { type: "TIKPOC_TRUSTED_SEND", text: "Second" },
    {
      url: "https://www.tiktok.com/messages",
      tab: { id: 84, url: "https://www.tiktok.com/messages" },
    },
  );
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(run.attached, [{ tabId: 42 }, { tabId: 84 }]);
  assert.equal((await second).ok, true);
  run.releaseFirstAttach();
  assert.equal((await first).ok, true);
});

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

function monitoringHarness() {
  let messageListener;
  let alarmListener;
  const createdUrls = [];
  const reloadedTabIds = [];
  const requests = [];
  const settings = {
    dashboardUrl: "http://127.0.0.1:8766",
    accountId: "account-01",
    enabled: false,
    monitoringStarted: false,
  };
  const tabs = [];
  const context = {
    URL,
    Set,
    Map,
    Date,
    chrome: {
      runtime: {
        onMessage: { addListener(listener) { messageListener = listener; } },
        onInstalled: { addListener() {} },
        onStartup: { addListener() {} },
        openOptionsPage() {},
      },
      storage: {
        local: {
          get(_keys, callback) { callback({ tikpocSettings: { ...settings } }); },
          set(update, callback) {
            Object.assign(settings, update.tikpocSettings || {});
            if (callback) callback();
          },
        },
        onChanged: { addListener(listener) { context.storageListener = listener; } },
      },
      alarms: {
        create() {},
        onAlarm: { addListener(listener) { alarmListener = listener; } },
      },
      tabs: {
        async query() { return tabs.map((tab) => ({ ...tab })); },
        async create({ url }) {
          const tab = { id: tabs.length + 1, url };
          tabs.push(tab);
          createdUrls.push(url);
          return tab;
        },
        async reload(tabId) { reloadedTabIds.push(tabId); },
        async sendMessage() {},
        onRemoved: { addListener() {} },
      },
      debugger: {
        async attach() {}, async sendCommand() {}, async detach() {},
      },
    },
    async fetch(url, options = {}) {
      requests.push({ url, options });
      return { ok: true, async json() { return { ok: true }; } };
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "background.js"), "utf8"),
    context,
  );
  function setMonitoring(started) {
    return new Promise((resolve) => {
      assert.equal(messageListener({
        type: "TIKPOC_SET_MONITORING",
        dashboardUrl: settings.dashboardUrl,
        started,
      }, {}, resolve), true);
    });
  }
  async function fireHealthAlarm() {
    alarmListener({ name: "tikpoc-browser-health" });
    await new Promise((resolve) => setImmediate(resolve));
  }
  return {
    createdUrls,
    fireHealthAlarm,
    reloadedTabIds,
    requests,
    setMonitoring,
    settings,
    tabs,
  };
}

test("one-click monitoring opens missing pages and enables the bound account", async () => {
  const run = monitoringHarness();

  const response = await run.setMonitoring(true);

  assert.equal(response.ok, true);
  assert.equal(run.settings.monitoringStarted, true);
  assert.equal(run.settings.enabled, true);
  assert.equal(run.settings.autoOpenActivity, true);
  assert.equal(run.settings.bindingMode, "auto");
  assert.deepEqual(run.createdUrls, [
    "https://www.tiktok.com/",
    "https://www.tiktok.com/messages",
  ]);
  assert.deepEqual(
    run.requests.filter(({ url }) => url.includes("/api/accounts/"))
      .map(({ url, options }) => [url, JSON.parse(options.body).enabled]),
    [
      ["http://127.0.0.1:8766/api/accounts/account-01/ai-enable", true],
      ["http://127.0.0.1:8766/api/accounts/account-01/followback-enable", true],
    ],
  );

  await run.setMonitoring(true);
  assert.equal(run.createdUrls.length, 2);
});

test("stopping monitoring disables actions without closing observer tabs", async () => {
  const run = monitoringHarness();
  await run.setMonitoring(true);

  const response = await run.setMonitoring(false);

  assert.equal(response.ok, true);
  assert.equal(run.settings.monitoringStarted, false);
  assert.equal(run.settings.enabled, false);
  assert.equal(run.tabs.length, 2);
  assert.deepEqual(
    run.requests.filter(({ url }) => url.includes("/api/accounts/"))
      .slice(-2)
      .map(({ options }) => JSON.parse(options.body).enabled),
    [false, false],
  );
});

test("starting monitoring refreshes reused observer pages once", async () => {
  const run = monitoringHarness();
  run.tabs.push(
    { id: 10, url: "https://www.tiktok.com/@shop" },
    { id: 11, url: "https://www.tiktok.com/messages" },
  );

  await run.setMonitoring(true);

  assert.deepEqual(run.createdUrls, []);
  assert.deepEqual(run.reloadedTabIds, [10, 11]);
});

test("concurrent monitoring recovery creates one observer pair", async () => {
  const run = monitoringHarness();

  const responses = await Promise.all([
    run.setMonitoring(true),
    run.setMonitoring(true),
  ]);

  assert.equal(responses.every(({ ok }) => ok), true);
  assert.deepEqual(run.createdUrls, [
    "https://www.tiktok.com/",
    "https://www.tiktok.com/messages",
  ]);
});

test("health recovery does not override account action switches", async () => {
  const run = monitoringHarness();
  await run.setMonitoring(true);
  const actionRequests = () => run.requests.filter(
    ({ url }) => url.includes("/api/accounts/"),
  ).length;
  assert.equal(actionRequests(), 2);

  await run.fireHealthAlarm();

  assert.equal(actionRequests(), 2);
});
