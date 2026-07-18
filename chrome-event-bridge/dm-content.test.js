const assert = require("node:assert/strict");
const test = require("node:test");

const dmContent = require("./dm-content.js");

const SETTINGS = {
  enabled: true,
  accountId: "account-01",
  deviceId: "phone-01",
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

function harness({ reads = [inbound(), inbound()], outbound = true, failResult = false } = {}) {
  const calls = [];
  const storage = memoryStorage();
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
        plan_id: 17,
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
    ownerId: "tab-01",
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
    timestamp_ms: 1_720_000_001_000,
  });
  assert.equal(JSON.stringify(payload).includes("Hello"), false);
  assert.equal(Object.hasOwn(payload, "text"), false);
});
