# Autonomous Asynchronous Customer Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining gaps between the implemented browser lead loop and the approved unattended customer-service contract.

**Architecture:** Keep Python as the durable policy, plan, lease, and funnel owner. Extend the existing Manifest V3 observers so both TikTok Messages route families receive the same account-scoped serialized workflow, and enforce stop-contact against pending welcomes transactionally in SQLite.

**Tech Stack:** Python 3.14, SQLite WAL, FastAPI, pytest, Manifest V3 JavaScript, Node test runner.

---

### Task 1: Inject Messages On Both TikTok Route Families

**Files:**
- Modify: `chrome-event-bridge/manifest.json`
- Modify: `chrome-event-bridge/manifest.test.js`
- Modify: `chrome-event-bridge/dm-content.test.js`

- [ ] **Step 1: Write the failing manifest test**

Assert that the DM content-script entry matches both route families and remains `all_frames: true`:

```js
assert.deepEqual(dmEntry.matches, [
  "https://www.tiktok.com/messages*",
  "https://www.tiktok.com/business-suite/messages*",
]);
```

Also retain `pageRole` cases for query strings, trailing slashes, and conversation subpaths.

- [ ] **Step 2: Run the test and observe the old single-route failure**

Run: `node --test chrome-event-bridge/manifest.test.js chrome-event-bridge/dm-content.test.js`

Expected: FAIL because the manifest injects only `/messages*`.

- [ ] **Step 3: Add the Business Suite match**

Set the DM entry matches to exactly the two TikTok URLs above. Keep the activity content script excluded from all `/business-suite/*` pages so only the DM observer owns Messages frames.

- [ ] **Step 4: Run focused and complete Node tests**

Run: `node --test chrome-event-bridge/manifest.test.js chrome-event-bridge/dm-content.test.js`

Run: `node --test chrome-event-bridge/*.test.js`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chrome-event-bridge/manifest.json chrome-event-bridge/manifest.test.js chrome-event-bridge/dm-content.test.js
git commit -m "fix: observe both TikTok message routes"
```

### Task 2: Coalesce Continuous Observer Triggers

**Files:**
- Modify: `chrome-event-bridge/content.js`
- Modify: `chrome-event-bridge/content.test.js`
- Modify: `chrome-event-bridge/dm-content.js`
- Modify: `chrome-event-bridge/dm-content.test.js`
- Modify: `chrome-event-bridge/background.test.js`

- [ ] **Step 1: Add failing trigger tests**

Build script harnesses that retain registered listeners. Assert:

```js
healthTickListener({ type: "TIKPOC_HEALTH_TICK" });
visibilityListener();
popstateListener();
assert.equal(maxConcurrentScans, 1);
assert.equal(scanCount, 2); // one active scan plus one coalesced rerun
```

For Activity, assert a health tick reports health and schedules follower work. For Messages, assert `visibilitychange`, `popstate`, `hashchange`, and a background health tick all schedule the same serialized workflow. Assert disabled settings still report health but perform no action.

- [ ] **Step 2: Run the focused Node tests and observe missing listeners/work scheduling**

Run: `node --test chrome-event-bridge/content.test.js chrome-event-bridge/dm-content.test.js chrome-event-bridge/background.test.js`

Expected: FAIL because Activity health ticks do not schedule a scan and neither content script registers route/visibility triggers.

- [ ] **Step 3: Implement bounded coalescing**

In Activity, replace the drop-on-busy guard with one active scan plus one queued rerun:

```js
let scanning = false;
let scanRequested = false;

async function drainScans() {
  if (scanning) {
    scanRequested = true;
    return;
  }
  scanning = true;
  try {
    do {
      scanRequested = false;
      await scanOnce();
    } while (scanRequested);
  } finally {
    scanning = false;
  }
}
```

Keep the existing 250 ms debounce and call it after health reporting. Register `visibilitychange`, `pageshow`, `popstate`, and `hashchange` triggers. In Messages, keep `createSerializedWorkflow()` as the exclusive queue and route every trigger through the existing debounced `schedule()` function. Do not add navigation buttons.

- [ ] **Step 4: Verify focused and complete extension suites**

Run: `node --test chrome-event-bridge/content.test.js chrome-event-bridge/dm-content.test.js chrome-event-bridge/background.test.js`

Run: `node --test chrome-event-bridge/*.test.js`

Expected: PASS with no overlapping scans or duplicate action calls.

- [ ] **Step 5: Commit**

```bash
git add chrome-event-bridge/content.js chrome-event-bridge/content.test.js chrome-event-bridge/dm-content.js chrome-event-bridge/dm-content.test.js chrome-event-bridge/background.test.js
git commit -m "fix: keep browser observers continuously scheduled"
```

### Task 3: Make Stop-Contact Block Pending Welcomes

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `tests/test_browser_dm.py`
- Modify: `tests/test_browser_welcome.py`

- [ ] **Step 1: Write the failing transactional test**

Create a completed follow-back and planned welcome, then plan an explicit stop-contact inbound for the same account and normalized participant. Assert:

```python
assert reply.reply_text == ""
assert reply.stage == "closed"
assert fake_ai.calls == []
assert database.browser_welcome_plan(account_id, username).state == "superseded"
assert database.next_browser_welcome_plan(account_id) is None
assert not database.claim_browser_welcome_action(
    account_id, f"welcome_send:{welcome.id}", "messages-tab", now_ms
)
```

Also assert another account with the same participant remains unchanged.

- [ ] **Step 2: Run the focused tests and observe the pending welcome**

Run: `uv run pytest tests/test_browser_dm.py tests/test_browser_welcome.py -q`

Expected: FAIL because finalizing a closed conversation does not update `browser_welcome_plans`.

- [ ] **Step 3: Supersede in the close transaction**

Within `Database.finalize_browser_reply_plan()`'s existing `BEGIN IMMEDIATE`, when the next conversation stage is `closed`, update only matching account/participant welcome rows in `planned` state to `superseded`. Preserve sent and uncertain history. Make `create_browser_welcome_plan()` and `next_browser_welcome_plan()` exclude participants with an existing closed conversation so a later follow event cannot recreate automatic contact.

- [ ] **Step 4: Run database, DM, welcome, and API regressions**

Run: `uv run pytest tests/test_browser_dm.py tests/test_browser_welcome.py tests/test_web_events_db.py tests/test_dashboard_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/db.py tests/test_browser_dm.py tests/test_browser_welcome.py
git commit -m "fix: enforce durable browser stop contact"
```

### Task 4: Regression And Controlled Live Acceptance

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Run complete automated verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
git diff --check
```

Expected: all commands exit zero. Retain the existing focused format baseline.

- [ ] **Step 2: Verify two controlled Chrome Profiles**

Require fresh Activity and Messages health for both accounts. In visible UI, verify one new follow-back and welcome, one background-tab inbound AI reply, one buying-intent profile/pinned-post route, reload idempotency, and one stop-contact message with no outbound reply. Record only state/count evidence, never message bodies or destinations.

- [ ] **Step 3: Record the result**

Document exact health counts, action states, reload result, and any remaining platform restriction. Do not claim the live gate from heartbeat or HTTP evidence alone.

- [ ] **Step 4: Commit**

```bash
git add docs/web-engagement-runbook.md AGENTS.md
git commit -m "docs: record autonomous browser acceptance"
```
