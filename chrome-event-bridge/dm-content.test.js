const assert = require("node:assert/strict");
const test = require("node:test");

const dmContent = require("./dm-content.js");

const SETTINGS = {
  enabled: true,
  accountId: "account-01",
  deviceId: "phone-01",
  expectedTikTokUsername: "shop_one",
  observedUsername: "shop_one",
  bindingState: "ready",
  dashboardUrl: "http://127.0.0.1:8766",
};

function inbound(overrides = {}) {
  return {
    accountId: SETTINGS.accountId,
    conversationId: "https://www.tiktok.com/messages/thread/one",
    sender: "buyer",
    messageId: "message-1",
    timestamp: "10:30",
    timestampMs: 1_720_000_000_000,
    text: "Hello",
    direction: "inbound",
    latest: true,
    ...overrides,
  };
}

function memoryStorage() {
  const values = {};
  return {
    values,
    async get(key) { return values[key]; },
    async set(key, value) { values[key] = value; },
  };
}

function harness({
  reads = [inbound(), inbound()],
  outbound = true,
  failResult = false,
  settings = SETTINGS,
  storage = memoryStorage(),
  planId = 17,
} = {}) {
  const calls = [];
  let currentRows = [{ key: inbound().conversationId, signature: "old", unread: false }];
  let readIndex = 0;
  let clicks = 0;
  const adapter = {
    conversationRows() { return currentRows; },
    rowSnapshot(row) { return row; },
    async openConversation() { return true; },
    readActiveConversation() {
      return reads[Math.min(readIndex++, reads.length - 1)];
    },
    findComposer() { return { value: "" }; },
    setComposerText(_composer, text) { return text === "Reply draft"; },
    findSendButton() { return { click() { clicks += 1; } }; },
    async waitForOutbound() { return outbound; },
  };
  const transport = async (type, body) => {
    calls.push({ type, body });
    if (failResult && type === "TIKPOC_DM_RESULT") {
      throw new Error("result response lost");
    }
    if (type === "TIKPOC_DM_PLAN") {
      return {
        plan_id: planId,
        conversation_id: body.conversation_id,
        inbound_fingerprint: body.fingerprint,
        reply_text: "Reply draft",
        stage: "engaged",
      };
    }
    if (type === "TIKPOC_ACTION_CLAIM") {
      return { claimed: true };
    }
    return { recorded: true };
  };
  const workflow = dmContent.createSerializedWorkflow({
    storage,
    transport,
    adapter,
    now: () => 1_720_000_000_500,
    ownerId: `tab-${settings.accountId}`,
  });
  return {
    adapter,
    calls,
    get clicks() { return clicks; },
    setRows(rows) { currentRows = rows; },
    storage,
    workflow,
  };
}

test("page adapter exposes semantic role, visibility, and labels", () => {
  assert.equal(dmContent.pageRole({ pathname: "/messages/thread/one" }), "messages");
  assert.equal(dmContent.pageRole({ pathname: "/business-suite/messages" }), "messages");
  assert.equal(dmContent.pageRole({ pathname: "/foryou" }), "other");
  const element = {
    textContent: "ignored",
    hidden: false,
    getAttribute(name) { return name === "aria-label" ? " Send " : null; },
    getClientRects() { return [{}]; },
    closest() { return null; },
    ownerDocument: { defaultView: { getComputedStyle() { return { display: "block", visibility: "visible" }; } } },
  };
  assert.equal(dmContent.visible(element), true);
  assert.equal(dmContent.elementLabel(element), "Send");
});

test("row identity uses the Messages URL when a DOM data id is also present", () => {
  const link = { href: "https://www.tiktok.com/messages/thread/one" };
  const row = {
    textContent: "Buyer Hello",
    matches() { return false; },
    getAttribute(name) {
      return name === "data-conversation-id" ? "opaque-dom-id" : null;
    },
    querySelector(selector) {
      return selector.includes("a[href") ? link : null;
    },
  };
  assert.equal(dmContent.rowSnapshot(row).key, link.href);
});

test("current TikTok conversation rows use a stable id without storing preview text", () => {
  const row = {
    textContent: "private preview body",
    hidden: false,
    matches() { return false; },
    getAttribute(name) {
      return {
        "data-conv-id": "conversation-1",
        "aria-selected": "true",
      }[name] || null;
    },
    getClientRects() { return [{}]; },
    closest() { return null; },
    querySelector(selector) {
      return selector.includes("dm-new-conversation-nickname")
        ? { textContent: "buyer", getAttribute() { return null; } }
        : null;
    },
    ownerDocument: {
      defaultView: {
        getComputedStyle() { return { display: "block", visibility: "visible" }; },
      },
    },
  };
  const documentValue = {
    querySelectorAll(selector) {
      assert.match(selector, /dm-new-conversation-item/);
      return [row];
    },
  };

  assert.deepEqual(dmContent.conversationRows(documentValue), [row]);
  const snapshot = dmContent.rowSnapshot(row);
  assert.equal(snapshot.key, "conv:conversation-1");
  assert.equal(snapshot.signature.includes("private preview body"), false);
});

test("reads the current TikTok inbound bubble from visible geometry", () => {
  const textNode = {
    textContent: "Hello",
    getBoundingClientRect() { return { left: 10, right: 30 }; },
  };
  const bubble = {
    textContent: "Hello",
    hidden: false,
    getAttribute() { return null; },
    getBoundingClientRect() { return { left: 0, right: 100 }; },
    getClientRects() { return [{}]; },
    closest() { return null; },
    querySelector(selector) {
      return selector.includes("message-text") ? textNode : null;
    },
    ownerDocument: {
      defaultView: {
        getComputedStyle() { return { display: "block", visibility: "visible" }; },
      },
    },
  };
  const participant = {
    textContent: "buyer",
    getAttribute() { return null; },
  };
  const selected = {
    getAttribute(name) { return name === "data-conv-id" ? "conversation-1" : null; },
  };
  const scope = {
    querySelector(selector) {
      return selector.includes("chat-uniqueid") ? participant : null;
    },
    querySelectorAll(selector) {
      assert.match(selector, /dm-new-chat-item/);
      return [bubble];
    },
  };
  const documentValue = {
    querySelector(selector) {
      if (selector === "main, [role='main']") return scope;
      if (selector.includes("aria-selected='true'")) return selected;
      return null;
    },
  };

  const active = dmContent.readActiveConversation(documentValue, "account-01");
  assert.equal(active.conversationId, "conv:conversation-1");
  assert.equal(active.direction, "inbound");
  assert.equal(active.text, "Hello");
});

test("opens the current TikTok conversation by clicking its row", async () => {
  let rowClicks = 0;
  let childClicks = 0;
  const child = {
    click() { childClicks += 1; },
    getClientRects() { return [{}]; },
  };
  const row = {
    hidden: false,
    matches(selector) { return selector.includes("dm-new-conversation-item"); },
    querySelector() { return child; },
    click() { rowClicks += 1; },
    getAttribute() { return null; },
    getClientRects() { return [{}]; },
    closest() { return null; },
    ownerDocument: {
      defaultView: {
        getComputedStyle() { return { display: "block", visibility: "visible" }; },
      },
    },
  };

  assert.equal(await dmContent.openConversation(row), true);
  assert.equal(rowClicks, 1);
  assert.equal(childClicks, 0);
});

test("startup establishes an account baseline without requesting a plan", async () => {
  const run = harness();
  assert.equal(await run.workflow.scan(SETTINGS), "baseline");
  assert.equal(run.calls.length, 0);
  assert.deepEqual(run.storage.values.tikpocDmBaselines[SETTINGS.accountId], {
    [inbound().conversationId]: "old",
  });
});

test("one inbound fingerprint is planned and sent only once", async () => {
  const run = harness();
  await run.workflow.scan(SETTINGS);
  run.setRows([{ key: inbound().conversationId, signature: "new", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "sent");
  assert.equal(run.clicks, 1);
  run.setRows([{ key: inbound().conversationId, signature: "newer-render", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "duplicate");
  assert.equal(run.clicks, 1);
  assert.equal(run.calls.filter((call) => call.type === "TIKPOC_DM_PLAN").length, 1);
  const processed = Object.values(run.storage.values.tikpocDmProcessed);
  assert.equal(processed.length, 1);
  assert.equal(processed[0].accountId, SETTINGS.accountId);
  for (const call of run.calls) {
    assert.equal(call.body.observed_username, "shop_one");
    assert.equal(call.body.binding_state, "ready");
  }
});

test("two accounts isolate equal inbound workflows in shared extension storage", async () => {
  const storage = memoryStorage();
  const settingsOne = SETTINGS;
  const settingsTwo = {
    ...SETTINGS,
    accountId: "account-02",
    deviceId: "phone-02",
    expectedTikTokUsername: "shop_two",
    observedUsername: "shop_two",
  };
  const sameInbound = {
    conversationId: inbound().conversationId,
    sender: "buyer",
    messageId: "message-1",
    timestamp: "10:30",
    timestampMs: 1_720_000_000_000,
    text: "Hello",
  };
  const first = harness({
    reads: [inbound({ ...sameInbound, accountId: settingsOne.accountId })],
    settings: settingsOne,
    storage,
    planId: 17,
  });
  const second = harness({
    reads: [inbound({ ...sameInbound, accountId: settingsTwo.accountId })],
    settings: settingsTwo,
    storage,
    planId: 18,
  });

  assert.equal(await first.workflow.scan(settingsOne), "baseline");
  assert.equal(await second.workflow.scan(settingsTwo), "baseline");
  first.setRows([{ key: sameInbound.conversationId, signature: "new", unread: true }]);
  second.setRows([{ key: sameInbound.conversationId, signature: "new", unread: true }]);
  assert.equal(await first.workflow.scan(settingsOne), "sent");
  assert.equal(await second.workflow.scan(settingsTwo), "sent");

  assert.deepEqual(Object.keys(storage.values.tikpocDmBaselines).sort(), [
    "account-01",
    "account-02",
  ]);
  const processed = Object.entries(storage.values.tikpocDmProcessed);
  assert.equal(processed.length, 2);
  assert.notEqual(processed[0][0], processed[1][0]);
  assert.deepEqual(
    processed.map(([, value]) => value.accountId).sort(),
    ["account-01", "account-02"],
  );
  const calls = [...first.calls, ...second.calls];
  for (const type of [
    "TIKPOC_DM_PLAN",
    "TIKPOC_ACTION_CLAIM",
    "TIKPOC_DM_RESULT",
    "TIKPOC_ACTION_RESULT",
  ]) {
    assert.deepEqual(
      calls.filter((call) => call.type === type).map((call) => call.body.account_id).sort(),
      ["account-01", "account-02"],
    );
  }
  assert.deepEqual(
    calls
      .filter((call) => call.type === "TIKPOC_ACTION_CLAIM")
      .map((call) => call.body.action_key)
      .sort(),
    ["dm_send:17", "dm_send:18"],
  );
});

test("a changed active inbound supersedes the plan before claim or send", async () => {
  const run = harness({ reads: [inbound(), inbound({ messageId: "message-2", text: "Changed" })] });
  await run.workflow.scan(SETTINGS);
  run.setRows([{ key: inbound().conversationId, signature: "new", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "superseded");
  assert.equal(run.clicks, 0);
  assert.equal(run.calls.some((call) => call.type === "TIKPOC_ACTION_CLAIM"), false);
  assert.equal(
    run.calls.find((call) => call.type === "TIKPOC_DM_RESULT").body.state,
    "superseded",
  );
});

test("does not plan while navigation is still on another conversation", async () => {
  const run = harness();
  await run.workflow.scan(SETTINGS);
  run.setRows([{ key: "https://www.tiktok.com/messages/thread/two", signature: "new", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "navigation_pending");
  assert.equal(run.calls.some((call) => call.type === "TIKPOC_DM_PLAN"), false);
  assert.equal(run.clicks, 0);
});

test("a matching outbound bubble records sent and completes the action", async () => {
  const run = harness({ outbound: true });
  await run.workflow.scan(SETTINGS);
  run.setRows([{ key: inbound().conversationId, signature: "new", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "sent");
  assert.equal(run.clicks, 1);
  assert.equal(run.calls.find((call) => call.type === "TIKPOC_DM_RESULT").body.state, "sent");
  assert.equal(
    run.calls.find((call) => call.type === "TIKPOC_ACTION_RESULT").body.state,
    "completed",
  );
});

test("missing outbound confirmation records uncertain and does not immediately retry", async () => {
  const run = harness({ outbound: false });
  await run.workflow.scan(SETTINGS);
  run.setRows([{ key: inbound().conversationId, signature: "new", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "uncertain");
  assert.equal(run.calls.find((call) => call.type === "TIKPOC_DM_RESULT").body.state, "uncertain");
  assert.equal(
    run.calls.find((call) => call.type === "TIKPOC_ACTION_RESULT").body.state,
    "uncertain",
  );
  run.setRows([{ key: inbound().conversationId, signature: "rerender", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "duplicate");
  assert.equal(run.clicks, 1);
});

test("a lost result response after click does not click the same fingerprint again", async () => {
  const run = harness({ failResult: true });
  await run.workflow.scan(SETTINGS);
  run.setRows([{ key: inbound().conversationId, signature: "new", unread: true }]);
  await assert.rejects(run.workflow.scan(SETTINGS), /result response lost/);
  assert.equal(run.clicks, 1);
  run.setRows([{ key: inbound().conversationId, signature: "rerender", unread: true }]);
  assert.equal(await run.workflow.scan(SETTINGS), "duplicate");
  assert.equal(run.clicks, 1);
});

test("health payload reports readiness without message content", () => {
  const payload = dmContent.buildHealthPayload(
    SETTINGS,
    { pathname: "/messages/thread/one" },
    true,
    1_720_000_001_000,
  );
  assert.deepEqual(payload, {
    account_id: "account-01",
    device_id: "phone-01",
    page_role: "messages",
    path: "/messages/thread/one",
    signed_in: true,
    observed_username: "shop_one",
    binding_state: "ready",
    timestamp_ms: 1_720_000_001_000,
  });
  assert.equal(JSON.stringify(payload).includes("Hello"), false);
  assert.equal(Object.hasOwn(payload, "text"), false);
});

test("disabled DM automation still reports binding health without running workflow", () => {
  const disabled = { ...SETTINGS, browserDmEnabled: false };

  assert.equal(dmContent.canReportHealth(disabled), true);
  assert.equal(dmContent.canRunWorkflow(disabled), false);
});

test("uses only fresh account-scoped activity binding evidence in a Messages frame", () => {
  const direct = { state: "unverified", observedUsername: "" };
  const cached = {
    accountId: SETTINGS.accountId,
    state: "ready",
    observedUsername: SETTINGS.observedUsername,
    observedAt: 1_000,
  };

  assert.deepEqual(
    dmContent.resolveBinding(SETTINGS, direct, cached, 2_000),
    { state: "ready", observedUsername: "shop_one", source: "cached" },
  );
  assert.deepEqual(
    dmContent.resolveBinding(SETTINGS, direct, cached, 122_001),
    { ...direct, source: "direct" },
  );
  assert.deepEqual(
    dmContent.resolveBinding(
      SETTINGS,
      direct,
      { ...cached, accountId: "account-02" },
      2_000,
    ),
    { ...direct, source: "direct" },
  );
});
