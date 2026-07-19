# Multi-Account Browser Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind any number of dedicated Chrome Profiles to configured TikTok accounts, block actions on visible-login mismatches, and prove two-account follow-back/reply isolation.

**Architecture:** The Python registry remains the source of account/device/expected-username mappings and exposes a redacted localhost binding endpoint. Each Chrome Profile selects one mapping in extension-local storage; pure DOM identity helpers gate every Activity or Messages action before the existing account-scoped leases and reply plans run.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, Chrome Manifest V3, plain JavaScript, Node test runner, React/TypeScript, Vitest, Playwright.

---

### Task 1: Unique N-Account Registry And Redacted Binding API

**Files:**
- Modify: `src/tikpoc/web_accounts.py`
- Modify: `src/tikpoc/api.py`
- Modify: `tests/test_web_accounts.py`
- Modify: `tests/test_acquisition_api.py`
- Modify: `config/web-accounts.example.yaml`

- [x] Add registry tests loading 1, 2, and 12 browser accounts and rejecting case-insensitive duplicate account IDs, device IDs, and expected TikTok usernames.
- [x] Add `expected_tiktok_username` and `browser_profile_label` to `WebAccount`; normalize usernames by trimming whitespace and one leading `@` without changing display case in the label.
- [x] Add `GET /api/browser-bindings` tests requiring only `account_id`, `device_id`, `expected_tiktok_username`, `browser_profile_label`, `enabled`, `browser_followback_enabled`, `browser_dm_enabled`, and `binding_ready`.
- [x] Implement the endpoint from `registry.accounts`; exclude offer, FAQ, private destination, message, token, cookie, and profile-path data.
- [x] Run `uv run pytest tests/test_web_accounts.py tests/test_acquisition_api.py -q` and commit `feat: expose browser account bindings`.

### Task 2: Pure Visible-Account Identity Gate

**Files:**
- Create: `chrome-event-bridge/binding-core.js`
- Create: `chrome-event-bridge/binding-core.test.js`
- Modify: `chrome-event-bridge/manifest.json`
- Modify: `chrome-event-bridge/content.js`
- Modify: `chrome-event-bridge/dm-content.js`

- [x] Add Node fixtures for one visible `/@username` account link, duplicate identical links, two different visible usernames, signed-out pages, verification pages, case normalization, and expected-username mismatch.
- [x] Implement `normalizeUsername`, `visibleAccountUsernames`, and `evaluateBinding`. Return `{state, observedUsername}` where state is `ready`, `unverified`, `mismatch`, `signed_out`, or `verification_required`.
- [x] Load `binding-core.js` before both content scripts and require `ready` before Activity baseline/action scanning or Messages baseline/plan/send scanning.
- [x] Add `observed_username` and `binding_state` to every browser health payload and action/plan identity body.
- [x] Run `node --check chrome-event-bridge/*.js` and `node --test chrome-event-bridge/*.test.js`; commit `feat: gate browser actions on visible account`.

### Task 3: Chrome Profile Binding Menu And Account-Scoped Reset

**Files:**
- Modify: `chrome-event-bridge/background.js`
- Modify: `chrome-event-bridge/background.test.js`
- Modify: `chrome-event-bridge/options.html`
- Modify: `chrome-event-bridge/options.js`
- Modify: `chrome-event-bridge/options.css`
- Modify: `chrome-event-bridge/popup.html`
- Modify: `chrome-event-bridge/popup.js`

- [x] Add background transport tests for a same-origin `GET /api/browser-bindings` message and rejection of non-loopback URLs.
- [x] Replace free-text account/device inputs with a Chinese account menu populated from the binding response; selecting an account stores the server-provided account, device, expected username, and profile label together.
- [x] Require an explicit Chinese confirmation before rebinding. On confirmation delete only the old account keys from `tikpocFollowerBaselines`, `tikpocProcessedFollowers`, `tikpocDmBaselines`, and `tikpocDmProcessed`.
- [x] Show profile label, account, expected username, observed username, and localized binding state in the popup with stable wrapping at 260 px.
- [x] Run Node tests and Playwright screenshot checks for populated, empty, and mismatch states; commit `feat: bind each Chrome profile to one account`.

### Task 4: Server-Side Binding Enforcement

**Files:**
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/db.py`
- Modify: `tests/test_acquisition_api.py`
- Modify: `tests/test_web_events_db.py`

- [ ] Add API tests proving missing, ambiguous, and mismatched visible identities receive `409`, while normalized matches can create a plan, claim an action, finish a result, and report health.
- [ ] Extend `BrowserIdentityRequest` with `observed_username` and `binding_state`; validate them against the registry in one `_browser_account` boundary used by events, plans, results, claims, and health.
- [ ] Persist browser health status and observed username per account/page role. Preserve the last `uncertain` action lease until reconciliation or expiry.
- [ ] Return `binding_unverified` for configured accounts lacking an expected username, and do not accept action-bearing requests for them.
- [ ] Run `uv run pytest tests/test_acquisition_api.py tests/test_web_events_db.py tests/test_browser_dm.py -q`; commit `feat: enforce browser profile identity`.

### Task 5: Chinese Multi-Account Health And Controls

**Files:**
- Modify: `src/tikpoc/acquisition_service.py`
- Modify: `operator-console/src/api.ts`
- Modify: `operator-console/src/components/RuntimeEvidence.tsx`
- Modify: `operator-console/src/views/InboxView.tsx`
- Modify: `operator-console/src/OperationsView.test.tsx`
- Modify: `operator-console/src/InboxView.test.tsx`
- Modify: `operator-console/src/styles.css`

- [ ] Add component tests for 12 accounts, both Activity/Messages rows, and Chinese states `未绑定`, `身份不符`, `已退出`, `需验证`, `已就绪`, and `心跳过期`.
- [ ] Extend browser health contracts with expected/observed username and binding state; render one compact row per account/page role without fixed account columns.
- [ ] Keep AI and follow-back account switches independent and disabled only for the affected unhealthy account; do not add browser fleet navigation controls.
- [ ] Run Vitest/build and Playwright at 1440x1000 and 390x844 with long account names; rebuild embedded assets and commit `feat: show multi-account browser readiness`.

### Task 6: Two-Account Isolation And Live Acceptance Gate

**Files:**
- Modify: `chrome-event-bridge/dm-content.test.js`
- Modify: `chrome-event-bridge/content.test.js`
- Modify: `tests/test_browser_dm.py`
- Modify: `tests/test_acquisition_api.py`
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md`

- [ ] Add two-account synthetic workflows using equal conversation IDs, message IDs, follower usernames, and timestamps; assert separate fingerprints, plans, baselines, leases, results, funnel events, and health.
- [ ] Run full Python, Node, frontend, Playwright, Android, Ruff, format-on-touched-files, and `git diff --check` verification.
- [ ] Document arbitrary account rollout, one Chrome Profile per mapping, baseline behavior, mismatch recovery, per-account stop switches, and the exact two-account checklist.
- [ ] Notify the user before live actions, open two dedicated Chrome Profiles, and request login only for profiles whose visible identity is absent or mismatched.
- [ ] Test both directions: new follow, one follow-back, three DMs, one reply per fingerprint, invitation policy, reload/rerender deduplication, contact/human handoff, and independent mobile progress.
- [ ] Record observed evidence without cookies, tokens, message bodies, private destinations, or screenshots containing personal data; commit `docs: record two-account browser acceptance`.
