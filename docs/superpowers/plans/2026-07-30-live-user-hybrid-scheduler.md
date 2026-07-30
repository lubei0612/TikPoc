# Live User Hybrid Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each VMOS APK process browser-sourced live-user priority touches before due brand comments, then resume comment pacing and Home browsing without losing durable state.

**Architecture:** Add a durable empty acquisition host round and a priority-only claim path that never consumes ordinary parent assignments. Extend mobile pull with `hybrid`, submit normalized live targets through an authenticated service, and dispatch the existing profile-touch and brand-comment envelopes through the current Android executor.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, Java/Android AccessibilityService, VMOS HTTPS workers.

---

## File Map

- `src/tikpoc/acquisition_db.py`: durable live host and priority-only claims.
- `src/tikpoc/live_batch_service.py`: normalize collector rows and create batches.
- `src/tikpoc/api_models.py`, `src/tikpoc/api.py`: hybrid pull and submission API.
- `src/tikpoc/cli.py`: host initialization, file submission, and status.
- `android-touch-executor/src/com/tikpoc/touch/DeviceApiClient.java`: hybrid pull.
- `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`: hybrid startup and idle browsing.
- `tests/` and Android Java tests: ordering, durability, and contract coverage.

### Task 1: Durable Live Host And Priority-Only Claim

**Files:** Modify `src/tikpoc/acquisition_db.py`; test `tests/test_priority_scheduler.py`.

- [ ] Write failing tests for `ensure_live_host_round(host_id, device_seeds, now_ms)`, exact idempotent replay, conflicting replay rejection, zero host assignments, live assignment claim, participant barrier, and no parent-target fallback.
- [ ] Run `uv run pytest tests/test_priority_scheduler.py -q`; expect missing-method failures.
- [ ] Add repository methods:

```python
def ensure_live_host_round(
    self, *, host_id: str, device_seeds: Mapping[str, str], now_ms: int
) -> str: ...

def claim_priority_assignment(
    self, parent_round_id: str, device_id: str, owner_id: str,
    *, now_ms: int, lease_ttl_ms: int = 120_000
) -> RoundAssignment | None: ...

def live_interrupt_pending(self, parent_round_id: str) -> bool: ...
```

  The host transaction inserts a deterministic zero-target pool, an active exposure round, and immutable device seeds. Priority-only selection reuses live FIFO and the participant barrier but returns `None` instead of claiming parent work.
- [ ] Run focused tests plus Ruff check/format and `git diff --check`.
- [ ] Commit `feat: add durable live-user host queue`.

### Task 2: Normalized In-Memory Live Submission

**Files:** Create `src/tikpoc/live_batch_service.py`; create `tests/test_live_batch_service.py`.

- [ ] Write failing tests for username validation, `sec_uid` identity preference, request deduplication, invalid counts, canonical checksum replay, running-device snapshots, and no-running-device rejection.
- [ ] Run `uv run pytest tests/test_live_batch_service.py -q`; expect import failure.
- [ ] Add immutable `LiveTargetInput` and `LiveBatchSummary` models and `LiveBatchService.submit(host_round_id, source_live_id, targets, navigation_mode)`. Canonicalize targets to NDJSON, hash the canonical bytes, import one pool, snapshot active host devices, derive distinct seeds, and call existing `create_priority_batch(..., batch_class=LIVE_INTERRUPT)`.
- [ ] Run focused tests and Ruff check/format.
- [ ] Commit `feat: submit normalized live-user batches`.

### Task 3: Hybrid Mobile And Collector APIs

**Files:** Modify `src/tikpoc/api_models.py`, `src/tikpoc/api.py`, `src/tikpoc/cli.py`, `tests/test_mobile_api.py`, and `tests/test_priority_cli.py`.

- [ ] Write failing tests for these exact outcomes: live returns first with `task_kind=profile_touch`; a participant at a live barrier gets no comment lease; no live work falls through to a due `brand_comment`; no due work returns empty; replay returns the same batch; an invalid submission bearer returns 401.
- [ ] Run `uv run pytest tests/test_mobile_api.py tests/test_priority_cli.py -q`; expect hybrid validation failures.
- [ ] Extend `MobilePullRequest.task_kind` to `Literal["touch", "brand_comment", "hybrid"]`. Hybrid requires `round_id`, claims priority-only work first, returns empty while any live batch blocks the host, and otherwise calls the existing comment claim path. Add `task_kind="profile_touch"` to touch envelopes.
- [ ] Add `POST /api/live-batches` with a constant-time bearer check against the write-only `live_batch_token` passed to `create_app`, request bounds of 1–10,000 targets, and a redacted summary response. Add `live-host-init`, `live-batch-submit`, and `live-batch-status` CLI commands over the same services.
- [ ] Run focused tests, Ruff, and diff checks.
- [ ] Commit `feat: arbitrate live touches before comments`.

### Task 4: Hybrid Android Worker

**Files:** Modify `DeviceApiClient.java`, `TikPocAccessibilityService.java`, `DeviceApiClientTest.java`, and `AutonomousTaskRunnerTest.java` under `android-touch-executor/`.

- [ ] Write failing Java tests proving `round_id="hybrid:ROUND"` posts `task_kind="hybrid"` plus `round_id="ROUND"`, then executes one `profile_touch` and one `brand_comment` result with local queue depth at most one.
- [ ] Run `bash android-touch-executor/build.sh`; expect assertion failure.
- [ ] Translate the provisioning prefix to a hybrid request while retaining the server payload. Treat hybrid like comment mode for startup Home recovery and idle read-only browsing. Keep existing executor dispatch: only `brand_comment` uses the comment executor; `profile_touch` uses the profile executor.
- [ ] Run the Android build and `git diff --check`.
- [ ] Commit `feat: run hybrid touch and comment work on device`.

### Task 5: Regression, Deployment, And Canary

**Files:** Modify `README.md`, `docs/priority-live-batch-cli.md`, and `AGENTS.md`.

- [ ] Document host initialization, JSONL/HTTP submission, priority, participant snapshots, barriers, comment resumption, browser collection, rollback, and redacted status.
- [ ] Run:

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
bash android-touch-executor/build.sh
node --test chrome-event-bridge/*.test.js
git diff --check
```

- [ ] Commit documentation and push `feat/web-lead-conversion`.
- [ ] Back up the production DB, deploy into a new release directory, set the live token only in the owner-readable service environment, initialize the six-device host, install/provision the verified APK with `hybrid:HOST_ROUND`, and retain the previous release/APK for rollback.
- [ ] Run a two-device synthetic canary proving preemption and comment fallback. Then collect about ten live users in the authenticated browser and verify exact identity, visible visit/action evidence, `N/N` coverage, errors, latency, and automatic comment resumption. Stop on inconsistent identity or action evidence.
