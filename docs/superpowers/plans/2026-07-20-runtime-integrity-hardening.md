# TikPoc Runtime Integrity Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four runtime integrity findings from the 2026-07-20 full repository review before live automation resumes.

**Architecture:** Keep browser requests in the extension background worker, move multi-row terminal operations into SQLite transactions, and carry the mobile device fence through the terminal persistence boundary. Existing account mappings, visible-state checks, immutable plans, and coverage semantics remain unchanged.

**Tech Stack:** Python 3.14, FastAPI, SQLite, Manifest V3 JavaScript, pytest, Node test runner, Ruff.

**Status:** Task 1 and Task 4 are implemented and verified. Tasks 2-3 are deferred per the user while the browser isolation approach is reconsidered.

---

### Task 1: Require An Explicit Extension Origin

**Files:**
- Modify: `src/tikpoc/api.py`
- Modify: `tests/test_lead_api.py`
- Modify: `tests/test_settings_api.py`
- Modify: `config/web-accounts.example.yaml` or the applicable environment example
- Modify: `docs/web-engagement-runbook.md`

- [x] Add failing API tests proving TikTok page origins receive `403` for bindings and every action-bearing browser endpoint.
- [x] Add a failing test proving one configured `chrome-extension://EXTENSION_ID` origin receives the expected CORS headers and can use the existing background-worker routes.
- [x] Remove TikTok origins from the default allowlist and accept only exact configured extension origins.
- [x] Document local extension-origin discovery/configuration without reading Chrome credentials or session data.
- [x] Run focused API tests, full Python, Chrome Node, Ruff, format, and diff checks; commit `fix: restrict browser APIs to the extension`.

### Task 2: Commit Followback Completion Atomically

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/browser_welcome.py`
- Modify: `chrome-event-bridge/background.js`
- Modify: `chrome-event-bridge/content.js`
- Modify: `tests/test_web_events_db.py`
- Modify: `tests/test_browser_welcome.py`
- Modify: `tests/test_lead_api.py`
- Modify: `chrome-event-bridge/content.test.js`

- [ ] Add failing database/API tests for event success plus lost result delivery, replay after expiry/reload, and exactly one welcome plan.
- [ ] Add one transactional repository operation that validates the followback lease, stores terminal evidence/event, and creates or reuses the welcome plan.
- [ ] Route verified followback completion through one background-worker request and persist local completed state only after success.
- [ ] Keep uncertain results on the existing busy/reconciliation path and create no welcome.
- [ ] Run focused Python/Node tests and full checks; commit `fix: commit followback completion atomically`.

### Task 3: Commit Monitoring Automation Switches Atomically

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/api.py`
- Modify: `chrome-event-bridge/background.js`
- Modify: `tests/test_settings_api.py`
- Modify: `chrome-event-bridge/background.test.js`

- [ ] Add failing API tests proving AI and followback switches change together or not at all under injected failure.
- [ ] Add failing background tests for server failure, local-storage failure, compensation, and unchanged previous local state.
- [ ] Implement one idempotent account automation endpoint backed by one SQLite transaction.
- [ ] Change one-click monitoring to use the combined endpoint and commit Chrome storage only after server success.
- [ ] Run focused Python/Node tests and full checks; commit `fix: switch browser monitoring atomically`.

### Task 4: Enforce The Device Fence Through Terminal Writes

**Files:**
- Modify: `src/tikpoc/fleet.py`
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `src/tikpoc/mobile_worker.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `tests/test_fleet.py`
- Modify: `tests/test_fleet_runtime.py`
- Modify: `tests/test_mobile_worker.py`
- Modify: `tests/test_acquisition_db.py`

- [x] Add failing concurrency tests that replace a device lease while a blocking operation is running and require the stale operation to raise after return.
- [x] Add failing repository tests proving a stale fence cannot record action results, mutate quota state, or complete/defer/skip an assignment.
- [x] Revalidate the fence after every device operation and carry fence identity into terminal repository transactions.
- [x] Let `DeviceWorkerLeaseLost` escape the assignment worker without writing stale defer evidence; stop the stale process cleanly.
- [x] Run focused concurrency/mobile/fleet tests and full checks; commit `fix: fence mobile terminal writes`.

### Task 5: Final Regression And Recovery Gate

- [ ] Run `uv run pytest -q`.
- [ ] Run `node --test chrome-event-bridge/*.test.js`.
- [ ] Run `bash android-event-bridge/build.sh`.
- [ ] Run `uv tool run ruff check src tests` and touched-file format checks.
- [ ] Run `git diff --check` and inspect the complete branch diff.
- [ ] Configure the exact local extension origin and verify configured extension requests succeed while TikTok page-origin requests return `403`.
- [ ] Recheck the production round checksum/state and all enabled device health before starting a short canary.
