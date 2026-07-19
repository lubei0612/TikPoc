# Browser Auto-Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically bind a loaded TikPoc Chrome extension to the unique configured account matching the visible TikTok username, with manual fallback, CLI health checks, and two-account live acceptance.

**Architecture:** A pure JavaScript core resolves a visible username against the redacted server binding list and computes an account-scoped atomic storage update. A small content coordinator applies that result before Activity and Messages workflows start. A Python browser-connect module powers nested CLI commands using only loopback HTTP APIs.

**Tech Stack:** Chrome Manifest V3, plain JavaScript, Node test runner, Python 3.12, argparse, urllib, FastAPI/SQLite, pytest.

---

### Task 1: Complete Browser Health Prerequisites

**Files:**
- Modify: `chrome-event-bridge/content.js`
- Modify: `chrome-event-bridge/content.test.js`
- Modify: `chrome-event-bridge/dm-content.js`
- Modify: `chrome-event-bridge/dm-content.test.js`

- [x] Add a VM-backed failing test proving Activity sends immediate and alarm-tick health while follow-back is disabled.
- [x] Implement account-scoped Activity health using visible binding state and the existing background transport.
- [x] Add a failing test proving disabled DM automation still reports health but does not scan or send.
- [x] Split `canReportHealth` from `canRunWorkflow` and pass the focused tests.
- [ ] Run `node --test chrome-event-bridge/*.test.js`, `node --check chrome-event-bridge/*.js`, and `git diff --check`.
- [ ] Commit `fix: report browser health before automation starts`.

### Task 2: Pure Automatic Binding Resolution

**Files:**
- Create: `chrome-event-bridge/auto-connect-core.js`
- Create: `chrome-event-bridge/auto-connect-core.test.js`
- Modify: `chrome-event-bridge/manifest.json`

- [ ] Add failing tests for unique normalized match, no match, duplicate match, disabled mapping, manual mode, same-account no-op, and account-scoped rebind reset.

```js
const result = core.resolveAutoBinding({
  observedUsername: "SHOP_ONE",
  bindings: [{
    account_id: "account-01",
    device_id: "phone-01",
    expected_tiktok_username: "shop_one",
    browser_profile_label: "TikPoc 01",
    enabled: true,
    binding_ready: true,
  }],
});
assert.equal(result.state, "matched");
assert.equal(result.binding.account_id, "account-01");
```

- [ ] Run `node --test chrome-event-bridge/auto-connect-core.test.js` and confirm failure because the module is missing.
- [ ] Implement `resolveAutoBinding`, `needsBindingUpdate`, and `autoBindingStorageUpdate`. Preserve the Dashboard URL and runtime switches, default fresh automation switches to false, set `enabled` true, and call `TikPocOptionsCore.resetForAccount` only when changing accounts.
- [ ] Load `auto-connect-core.js` from the manifest before the coordinator and both workflow scripts.
- [ ] Run the focused test and commit `feat: resolve visible browser account bindings`.

### Task 3: Automatic Content-Script Connection And Manual Fallback

**Files:**
- Create: `chrome-event-bridge/auto-connect.js`
- Create: `chrome-event-bridge/auto-connect.test.js`
- Modify: `chrome-event-bridge/manifest.json`
- Modify: `chrome-event-bridge/options.html`
- Modify: `chrome-event-bridge/options.js`
- Modify: `chrome-event-bridge/options-core.js`
- Modify: `chrome-event-bridge/options-core.test.js`
- Modify: `chrome-event-bridge/popup.html`
- Modify: `chrome-event-bridge/popup.js`

- [ ] Add a failing coordinator test with fake Chrome storage and background transport. Assert one visible username fetches `/api/browser-bindings`, stores the unique server mapping atomically, and emits a storage change consumed by Activity and Messages.

```js
assert.deepEqual(saved.tikpocSettings, {
  accountId: "account-02",
  deviceId: "phone-02",
  expectedTikTokUsername: "ikun.bags5",
  browserProfileLabel: "Your Chrome",
  dashboardUrl: "http://127.0.0.1:8766",
  bindingMode: "auto",
  enabled: true,
  browserFollowbackEnabled: false,
  browserDmEnabled: false,
});
```

- [ ] Implement a serialized `connect()` coordinator that reads visible usernames through `TikPocBindingCore`, requests bindings through `TIKPOC_GET_BINDINGS`, applies the pure update, and retries only after DOM or relevant storage changes.
- [ ] Add negative tests proving signed-out, verification, ambiguous, and missing mappings update status without writing an account mapping or running actions.
- [ ] Add an “自动识别当前 TikTok 账号” switch to settings. Automatic mode shows observed username and matched account; manual mode enables the existing server-provided menu and retains confirmation/reset behavior.
- [ ] Update the popup to show `自动识别` or `人工绑定` and the last auto-connect state without exposing editable identifiers.
- [ ] Run all extension tests and commit `feat: auto-connect visible TikTok accounts`.

### Task 4: Browser Connection CLI

**Files:**
- Create: `src/tikpoc/browser_connect.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_cli.py`

- [ ] Add failing CLI tests for `browser status`, successful `browser connect`, timeout, registry/API mismatch, and `browser guide`.

```python
exit_code = main([
    "browser", "connect",
    "--web-accounts", str(config_path),
    "--dashboard-url", server_url,
    "--timeout", "1",
    "--poll-interval", "0.01",
])
assert exit_code == 0
assert "ready=4/4" in capsys.readouterr().out
```

- [ ] Implement `fetch_json`, `redacted_browser_status`, and `wait_for_browser_health` in `browser_connect.py` with `urllib.request`, monotonic timeout, and deterministic injectable sleep/clock for tests.
- [ ] Add nested argparse commands:

```text
tikpoc browser connect --web-accounts CONFIG --dashboard-url URL --timeout 60
tikpoc browser status --dashboard-url URL [--json]
tikpoc browser guide [--extension-path PATH]
```

- [ ] Validate that configured account IDs and expected usernames match `/api/browser-bindings`; require Activity and Messages `ready` rows for every enabled browser account.
- [ ] Print only account ID, Profile label, expected/observed username, page role, binding state, and heartbeat age. Never print messages, destinations, credentials, or offer/FAQ content.
- [ ] Run focused Python tests and commit `feat: operate browser connections from CLI`.

### Task 5: Manual And AI Connection Guide

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md`

- [ ] Document the one-time unpacked-extension load using `Command+Shift+G`, automatic visible-username matching, manual fallback, and CLI commands.
- [ ] Document the exact AI instruction “连接这个 Chrome” and its service, reload, health, and blocked-state procedure.
- [ ] Document reload/update recovery and make clear that real actions are enabled only after ready health and explicit operator approval.
- [ ] Run `git diff --check` and commit `docs: guide browser auto-connect operations`.

### Task 6: Two-Account Live Acceptance And Final Checkpoint

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md`

- [x] Reload both dedicated Profiles and run `tikpoc browser connect` until `account-01` and `account-02` each report ready Activity and Messages health.
- [x] Enable account-scoped AI reply settings only after fresh DM baselines exist; leave follow-back paused after the mutual-follow gate.
- [x] From each account, follow the other account once and verify the visible following/friend result per direction.
- [ ] Send three concise acceptance DMs per direction and verify one immutable plan, one lease, one visible outbound response, and one completed result per inbound fingerprint.
- [ ] Reload both pages and confirm no duplicate automatic plan or send. Mutual-follow and bidirectional manual-DM state already survived reopening without repetition.
- [ ] Verify invitation cooldown only if a real private destination is locally configured; otherwise record the gate as not exercised rather than inventing a destination.
- [ ] Send a synthetic contact-format acceptance message and a human-request message, verify `contact_captured` then `human_required`, and verify ordinary AI replies stop after handoff.
- [x] Record only aliases, timestamps, states, counts, and mobile progress; omit message bodies, contacts, destinations, cookies, and tokens.
- [ ] Run full Python, Node, frontend, Playwright, Android, Ruff, touched-file format, syntax, and `git diff --check` verification.
- [ ] Update the Task 6 checklist and checkpoint, then commit `docs: record two-account browser acceptance`.

Live checkpoint on 2026-07-19: `4/4` browser health, mutual visible follow,
and bidirectional manual DM delivery passed. The existing final inbound was
baselined while identity binding recovered, so aggregate persistence remained at
zero reply plans and zero DM action leases. No automatic reply was observed.
Remote model credentials and a private destination were not configured;
follow-back remains paused and the remaining automatic-reply gates stay open.
