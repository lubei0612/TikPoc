# Trusted Browser Message Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Submit autonomous TikTok AI replies through Chrome-trusted keyboard input while preserving multi-account leases, exact visible reconciliation, and reload idempotency.

**Architecture:** The Messages content script keeps observation, target validation, planning, and result reconciliation. Its background service worker gains a route-validated, per-tab serialized `chrome.debugger` input boundary that clears the focused editor, inserts the durable reply text, presses Enter, and detaches immediately. Synthetic DOM composition is removed from the production send path.

**Tech Stack:** Chrome Manifest V3, `chrome.debugger`, Chrome DevTools Protocol Input commands, plain JavaScript, Node test runner, pytest, SQLite, FastAPI.

---

### Task 1: Background Trusted-Input Boundary

**Files:**
- Modify: `chrome-event-bridge/manifest.json`
- Modify: `chrome-event-bridge/background.js`
- Modify: `chrome-event-bridge/background.test.js`

- [ ] **Step 1: Write failing manifest and trusted-send tests**

Add tests proving the manifest includes `debugger`, a TikTok Messages sender can request `TIKPOC_TRUSTED_SEND`, non-Messages and missing-tab senders are rejected, commands run in this order, and detach always runs:

```js
assert.ok(manifest.permissions.includes("debugger"));
assert.deepEqual(commands.map(({ method }) => method), [
  "Input.dispatchKeyEvent",
  "Input.dispatchKeyEvent",
  "Input.dispatchKeyEvent",
  "Input.dispatchKeyEvent",
  "Input.insertText",
  "Input.dispatchKeyEvent",
  "Input.dispatchKeyEvent",
]);
assert.equal(attached[0].tabId, 42);
assert.equal(detached[0].tabId, 42);
```

Add a deferred-command test that starts two sends in the same tab and asserts the second `attach` begins only after the first `detach`. Add a separate-tab test proving tab `42` and tab `84` do not share one queue.

- [ ] **Step 2: Run the focused tests and observe failure**

Run:

```bash
node --test --test-name-pattern='trusted|debugger' chrome-event-bridge/background.test.js
```

Expected: the manifest permission and trusted-send route assertions fail because the debugger boundary is absent.

- [ ] **Step 3: Implement the validated serialized debugger sender**

Add `"debugger"` to `manifest.json`. In `background.js`, add:

```js
const TRUSTED_SEND = "TIKPOC_TRUSTED_SEND";
const trustedSendQueues = new Map();

function trustedMessagesUrl(value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch (_error) {
    return false;
  }
  return url.origin === "https://www.tiktok.com" &&
    (url.pathname.startsWith("/messages") ||
      url.pathname.startsWith("/business-suite/messages"));
}

async function runTrustedSend(tabId, text) {
  const target = { tabId };
  let attached = false;
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "rawKeyDown", key: "a", code: "KeyA", modifiers: 4,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp", key: "a", code: "KeyA", modifiers: 4,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "rawKeyDown", key: "Backspace", code: "Backspace",
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp", key: "Backspace", code: "Backspace",
    });
    await chrome.debugger.sendCommand(target, "Input.insertText", { text });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "rawKeyDown", key: "Enter", code: "Enter",
      windowsVirtualKeyCode: 13,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp", key: "Enter", code: "Enter",
      windowsVirtualKeyCode: 13,
    });
    return { submitted: true };
  } finally {
    if (attached) {
      await chrome.debugger.detach(target);
    }
  }
}
```

Validate `sender.tab.id`, `sender.url || sender.tab.url`, normalized text length `1..6000`, and queue `runTrustedSend` per tab. Route `TRUSTED_SEND` before the existing localhost POST routes and return its result through `sendResponse`.

- [ ] **Step 4: Run focused and complete background tests**

Run:

```bash
node --check chrome-event-bridge/background.js
node --test chrome-event-bridge/background.test.js
```

Expected: route validation, command order, detach behavior, same-tab serialization, and separate-tab independence pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add chrome-event-bridge/manifest.json chrome-event-bridge/background.js chrome-event-bridge/background.test.js
git commit -m "feat: add trusted browser message input"
```

### Task 2: Messages Workflow Uses Trusted Send Only

**Files:**
- Modify: `chrome-event-bridge/dm-content.js`
- Modify: `chrome-event-bridge/dm-content.test.js`

- [ ] **Step 1: Write failing workflow tests**

Change the test harness to expose `prepareComposer` and `sendTrusted`. Add assertions proving one planned reply and one welcome call trusted send exactly once, while `setComposerText` and `findSendButton` are never used:

```js
assert.deepEqual(run.trustedTexts, ["Reply draft"]);
assert.equal(run.syntheticComposeCalls, 0);
assert.equal(run.clicks, 0);
```

Add cases proving a missing composer or `{submitted: false}` records `uncertain`, and a successful trusted submission still requires `waitForOutbound` before recording `sent/completed`.

- [ ] **Step 2: Run focused tests and observe failure**

Run:

```bash
node --test --test-name-pattern='trusted|planned reply|welcome' chrome-event-bridge/dm-content.test.js
```

Expected: trusted-send assertions fail because the workflow still mutates the DOM and clicks a send button.

- [ ] **Step 3: Implement the trusted-send adapter and workflow**

Add:

```js
function prepareComposer(documentValue = document) {
  const composer = findComposer(documentValue);
  if (!composer) {
    return false;
  }
  composer.focus();
  return true;
}

async function sendTrusted(text) {
  const response = await chrome.runtime.sendMessage({
    type: "TIKPOC_TRUSTED_SEND",
    text: core.normalizeText(text),
  });
  if (!response || !response.ok) {
    throw new Error(response && response.error || "trusted send failed");
  }
  return Boolean(response.result && response.result.submitted);
}
```

For reply and welcome sends, replace composer mutation/button click with:

```js
const prepared = adapter.prepareComposer();
const submitted = prepared && await adapter.sendTrusted(replyText);
const confirmed = submitted && await adapter.waitForOutbound(replyText);
```

Keep existing `uncertain`, processed-record, action-result, and exact-outbound reconciliation behavior. Do not fall back to `textContent`, synthetic events, or direct `.click()`.

- [ ] **Step 4: Run the complete Chrome suite**

Run:

```bash
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
```

Expected: all Chrome tests pass and the production workflow has no synthetic-send fallback.

- [ ] **Step 5: Commit Task 2**

```bash
git add chrome-event-bridge/dm-content.js chrome-event-bridge/dm-content.test.js
git commit -m "fix: send browser replies with trusted input"
```

### Task 3: One-Click Multi-Account Monitoring

**Files:**
- Create: `chrome-event-bridge/popup-core.js`
- Create: `chrome-event-bridge/popup-core.test.js`
- Modify: `chrome-event-bridge/popup.html`
- Modify: `chrome-event-bridge/popup.js`
- Modify: `chrome-event-bridge/background.js`
- Modify: `chrome-event-bridge/background.test.js`

- [ ] **Step 1: Write failing popup-state and monitoring tests**

Add pure popup tests proving `monitoringStarted` selects `开始监控` or `停止监控` and exposes a pending/error label. Add background tests proving start verifies the loopback service, persists `enabled=true` and `monitoringStarted=true`, reuses existing observer tabs, creates only missing `/` and `/messages` tabs, and enables both account endpoints after a binding appears. Add stop tests proving both endpoints receive `enabled=false` and tabs remain open.

```js
assert.deepEqual(core.monitoringButton({ monitoringStarted: false }), {
  label: "开始监控",
  action: "start",
});
assert.deepEqual(createdUrls, [
  "https://www.tiktok.com/",
  "https://www.tiktok.com/messages",
]);
```

Add an alarm/startup/removal test proving repeated recovery calls create no duplicate observer pages.

- [ ] **Step 2: Run focused tests and observe failure**

Run:

```bash
node --test chrome-event-bridge/popup-core.test.js chrome-event-bridge/background.test.js
```

Expected: popup core and monitoring message behavior are absent.

- [ ] **Step 3: Implement popup state and background monitoring lifecycle**

Create `popup-core.js` with `monitoringButton(settings, pending)` and include it before `popup.js`. Add `#toggle-monitoring` and `#monitoring-detail` to the popup. The click sends:

```js
{
  type: "TIKPOC_SET_MONITORING",
  dashboardUrl: settings.dashboardUrl || "http://127.0.0.1:8766",
  started: !settings.monitoringStarted,
}
```

In `background.js`, validate the local dashboard, ping `/api/status`, update `tikpocSettings`, and implement `ensureMonitoringTabs()` with `chrome.tabs.query` and `chrome.tabs.create`. Reuse one `/messages*` or `/business-suite/messages*` tab and one non-Messages TikTok tab; create only missing routes.

When `monitoringStarted` and `accountId` are present, POST unique commands to:

```text
/api/accounts/<account_id>/ai-enable
/api/accounts/<account_id>/followback-enable
```

Use `enabled: true` for start and `enabled: false` for stop. Run the idempotent recovery on Chrome startup, the health alarm, settings binding changes, and monitored-tab removal. Do not close tabs on stop.

- [ ] **Step 4: Run the complete Chrome suite**

Run:

```bash
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
```

Expected: one-click start/stop, tab recovery, trusted input, and existing account isolation tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add chrome-event-bridge/popup-core.js chrome-event-bridge/popup-core.test.js chrome-event-bridge/popup.html chrome-event-bridge/popup.js chrome-event-bridge/background.js chrome-event-bridge/background.test.js
git commit -m "feat: start multi-account monitoring from the popup"
```

### Task 4: Regression, Runtime Enablement, And Live Acceptance

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md` only through an implementation-owned checkpoint hunk that does not stage unrelated edits

- [ ] **Step 1: Run the complete automated regression**

Run:

```bash
uv run pytest -q
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
npm --prefix operator-console test -- --run
npm --prefix operator-console run build
bash android-event-bridge/build.sh
uv tool run ruff check src tests
git diff --check
```

Expected: Python, Chrome, frontend, production build, Android build, Ruff, and whitespace checks pass.

- [ ] **Step 2: Reload both controlled extensions and start from the popup**

In both controlled Profiles, reload the unpacked extension to grant Debugger permission. Close or leave the observer pages in mixed states, click `开始监控`, and verify the popup creates or reuses one Activity-capable page plus one Messages page per Profile. Run `tikpoc browser connect` and require fresh `ready=4/4`. Record the current maximum reply-plan ID, wait at least one watchdog interval, and verify history creates no new plan.

- [ ] **Step 3: Perform exact trusted-input live acceptance**

Pause AI on the sending account so its observer cannot switch the operator-controlled conversation. Revalidate the exact target profile link in both tabs, enable AI only on the receiving account, and send one fresh buying-interest message. Verify:

1. one new durable plan is created for the receiving account;
2. the background worker attaches, inserts, submits, and detaches;
3. no TikTok error appears;
4. the exact reply is visible once on both sides;
5. the plan is `sent` and `dm_send:<plan_id>` is `completed` without manual reconciliation;
6. page reload leaves one reply and creates no new plan or lease.

Repeat in the opposite direction. Then restore AI reply and automatic follow-back for both accounts and confirm `/api/browser-bindings` reports both enabled and binding-ready.

- [ ] **Step 4: Exercise a fresh follow when available**

Use a controlled new follow event without unfollowing an existing relationship. Verify one visible follow-back, one `completed` follow-back lease, one product-interest welcome, and reload idempotency. If no fresh controlled follow exists, leave this live gate explicitly open while retaining both account-scoped runtime switches.

- [ ] **Step 5: Update documentation and commit**

Record Debugger permission, transient attach/detach behavior, Chrome-running boundary, `4/4`, test counts, plan/lease evidence, duplicate checks, and any open follow gate. Stage only implementation-owned documentation:

```bash
git diff --check
git status --short --branch
git add docs/web-engagement-runbook.md
git commit -m "docs: record trusted input live acceptance"
```
