# Autonomous APK Task Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move production mobile execution from runtime SSH/ADB commands to an APK-initiated HTTPS pull/execute/report loop while preserving evidence, leases, coverage, priority interruption/resume, and the 10,000-target daily capacity gate.

**Architecture:** Extend the existing FastAPI/SQLite acquisition service with device registration, scoped authentication, task leasing, heartbeats, and idempotent result ingestion. Add a small Android client inside the existing accessibility APK with a durable local queue/outbox and HTTPS polling; reuse the current `TouchCommandDispatcher` and semantic evidence code. Keep ADB and VMOS OpenAPI in provisioning/diagnostics tooling only.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite WAL, Android Java 8, `HttpsURLConnection`, Android Keystore, existing AccessibilityService, pytest, Java build script.

---

## Task 1: Freeze the current ADB path as diagnostics-only

**Files:**
- Modify: `src/tikpoc/device_side_transport.py`
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `tests/test_device_side_transport.py`
- Modify: `tests/test_fleet_runtime.py`

- [ ] **Step 1: Write the failing tests**

Add tests asserting that a health probe cannot create a second forward for a worker-owned `(adb_endpoint, host_port)` claim, that a diagnostic transport uses a distinct explicit port, and that fleet runtime does not call transport health while a worker transport is active.

- [ ] **Step 2: Run focused tests to verify the failure**

Run:

```bash
uv run pytest tests/test_device_side_transport.py tests/test_fleet_runtime.py -q
```

Expected: the new collision-isolation assertions fail before implementation.

- [ ] **Step 3: Implement the smallest isolation boundary**

Add a `diagnostic_only: bool = False` constructor field to `DeviceSideTransport`; reject `start()` for diagnostic-only transports when the configured port is a worker port unless an explicit diagnostic port is supplied. Add a `FleetRuntimeDiagnostics` helper that reads persisted device HTTPS health when available and never calls `adb forward` on a worker port. Keep the existing worker transport behavior unchanged.

- [ ] **Step 4: Run focused tests and lint**

```bash
uv run pytest tests/test_device_side_transport.py tests/test_fleet_runtime.py -q
uv tool run ruff check src/tikpoc/device_side_transport.py src/tikpoc/fleet_runtime.py tests/test_device_side_transport.py tests/test_fleet_runtime.py
```

Expected: PASS and no Ruff findings.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/device_side_transport.py src/tikpoc/fleet_runtime.py tests/test_device_side_transport.py tests/test_fleet_runtime.py
git commit -m "fix: isolate device diagnostics from worker transport"
```

## Task 2: Add durable device registration and session epochs

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Create: `src/tikpoc/device_api.py`
- Modify: `tests/test_acquisition_db.py`
- Create: `tests/test_device_api.py`

- [ ] **Step 1: Write the failing repository tests**

Cover these exact behaviors:

```python
def test_register_device_rotates_session_epoch_and_revokes_old_token(): ...
def test_result_from_old_epoch_is_rejected_without_state_change(): ...
def test_register_rejects_account_binding_mismatch(): ...
```

The tests must verify a device row containing `device_id`, `account_id`, `session_epoch`, a hashed token, `state`, `last_seen_at_ms`, and `revoked_at_ms`, and must never expose the raw token in returned database rows.

- [ ] **Step 2: Run the new tests to confirm failure**

```bash
uv run pytest tests/test_acquisition_db.py tests/test_device_api.py -q
```

Expected: missing repository methods and API models produce failures.

- [ ] **Step 3: Add the repository migration and service types**

Create SQLite tables `mobile_devices`, `mobile_leases`, `mobile_task_receipts`, and `mobile_heartbeats` in `AcquisitionRepository.migrate()`. Add typed dataclasses in `device_api.py` for `DeviceRegistration`, `DeviceSession`, `TaskEnvelope`, `TaskResult`, and `Heartbeat`. Store only a SHA-256 token digest. Generate a cryptographically random token and increment `session_epoch` atomically during registration.

- [ ] **Step 4: Implement repository methods**

Implement these exact methods:

```python
register_mobile_device(device_id: str, account_id: str, now_ms: int) -> DeviceSession
authenticate_mobile_device(device_id: str, token: str, now_ms: int) -> DeviceSession | None
revoke_mobile_device(device_id: str, now_ms: int) -> None
claim_mobile_tasks(device_id: str, session_epoch: int, limit: int, now_ms: int) -> tuple[TaskEnvelope, ...]
record_mobile_heartbeat(device_id: str, session_epoch: int, payload: Mapping[str, object], now_ms: int) -> bool
record_mobile_result(result: TaskResult, now_ms: int) -> str
```

Use a unique `(device_id, session_epoch, idempotency_key)` receipt constraint. A stale epoch returns `stale_session` and performs no mutation.

- [ ] **Step 5: Run tests and commit**

```bash
uv run pytest tests/test_acquisition_db.py tests/test_device_api.py -q
uv tool run ruff check src/tikpoc/device_api.py src/tikpoc/acquisition_db.py tests/test_device_api.py
 git add src/tikpoc/device_api.py src/tikpoc/acquisition_db.py tests/test_acquisition_db.py tests/test_device_api.py
 git commit -m "feat: add durable mobile device sessions"
```

## Task 3: Expose authenticated mobile HTTPS endpoints

**Files:**
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `tests/test_acquisition_api.py`
- Modify: `src/tikpoc/acquisition_db.py`

- [ ] **Step 1: Write endpoint contract tests**

Add FastAPI tests for:

- `POST /api/mobile/register` returning the token exactly once.
- `POST /api/mobile/pull` returning at most the requested bounded number of typed envelopes.
- `POST /api/mobile/heartbeat` rejecting stale epochs.
- `POST /api/mobile/results` accepting a first idempotency key and returning `duplicate` on replay.
- All protected routes rejecting missing, malformed, revoked, or wrong-device bearer tokens without revealing account data.

- [ ] **Step 2: Run the endpoint tests and observe failure**

```bash
uv run pytest tests/test_acquisition_api.py -q -k mobile
```

Expected: 404 or validation failures because the routes and request models do not exist.

- [ ] **Step 3: Add strict Pydantic request/response models**

Add `MobileRegisterRequest`, `MobilePullRequest`, `MobileHeartbeatRequest`, and `MobileResultRequest` with strict bounded identifiers, positive epochs, bounded queue limits (`1..50`), and JSON payloads limited to typed evidence fields. Define response envelopes with `session_epoch`, `lease_id`, `lease_expires_at_ms`, `task_id`, `plan_id`, target identity, policy version, phase, and immutable outcome plan.

- [ ] **Step 4: Add route authentication and handlers**

Add a private `_mobile_session(request)` helper that checks `Authorization: Bearer <token>`, authenticates through the repository, and returns a 401/409 without including token material. Implement register, pull, heartbeat, and results handlers. Pull must stop at 50 tasks, reserve server leases, and return priority envelopes before normal envelopes. Result ingestion must pass the session epoch and idempotency key to the repository.

- [ ] **Step 5: Run focused tests, full API tests, and commit**

```bash
uv run pytest tests/test_acquisition_api.py -q
uv run pytest tests/test_acquisition_db.py tests/test_acquisition_api.py -q
 git add src/tikpoc/api_models.py src/tikpoc/api.py src/tikpoc/acquisition_db.py tests/test_acquisition_api.py
 git commit -m "feat: expose authenticated mobile task API"
```

## Task 4: Add APK durable queue, provisioning, and HTTPS client

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/DeviceTaskStore.java`
- Create: `android-touch-executor/src/com/tikpoc/touch/DeviceApiClient.java`
- Create: `android-touch-executor/src/com/tikpoc/touch/DeviceProvisioning.java`
- Modify: `android-touch-executor/AndroidManifest.xml`
- Modify: `android-touch-executor/build.sh`
- Create: `android-touch-executor/test/com/tikpoc/touch/DeviceTaskStoreTest.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/DeviceApiClientTest.java`

- [ ] **Step 1: Write Java unit tests first**

Cover: queue survives restart, duplicate result IDs collapse to one outbox row, expired leases are not claimed, old session epochs are rejected, and a server response with the same result ID is treated as a replay.

- [ ] **Step 2: Run Android tests to confirm failure**

```bash
bash android-touch-executor/build.sh
```

Expected: compilation fails because the new classes are absent.

- [ ] **Step 3: Implement `DeviceTaskStore`**

Use `SharedPreferences` for provisioning metadata and a private SQLite database for `tasks`, `checkpoints`, and `result_outbox`. Persist `task_id`, `plan_id`, `lease_id`, `session_epoch`, `phase`, serialized typed task JSON, `attempt_id`, and timestamps. Store the bearer token only through Android Keystore; the database stores no token.

- [ ] **Step 4: Implement `DeviceApiClient`**

Use `HttpsURLConnection` with connect/read timeouts, certificate validation, bounded response sizes, exponential backoff with jitter, and JSON framing matching the server models. Implement `register`, `pull`, `heartbeat`, and `uploadResult`. Require HTTPS in production configuration. Allow a local synthetic HTTP endpoint only in Java tests.

- [ ] **Step 5: Implement one-time provisioning**

Add a non-exported `DeviceProvisioning` receiver/activity path that accepts only an operator-supplied device ID, account ID, API base URL, and one-time bootstrap token. Persist the resulting scoped token in Keystore and disable the bootstrap token after registration. Do not read or store TikTok credentials.

- [ ] **Step 6: Update build and run Java tests**

Add the new source/test files to `build.sh`, then run:

```bash
bash android-touch-executor/build.sh
```

Expected: all existing and new Java tests PASS and a signed APK is produced.

- [ ] **Step 7: Commit**

```bash
git add android-touch-executor/src android-touch-executor/test android-touch-executor/AndroidManifest.xml android-touch-executor/build.sh
git commit -m "feat: add durable APK task client transport"
```

## Task 5: Implement APK autonomous executor loop

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskRunner.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/AutonomousTaskRunnerTest.java`

- [ ] **Step 1: Write runner state-machine tests**

Test the exact sequence `profile_opening → identity_confirmed → video_opening → video_confirmed → quota_reserved → action_executing → completed`, explicit unavailable/deferred terminal states, one read-only uncertain reconciliation, no next-task claim during an in-flight action, and priority task selection at the target boundary.

- [ ] **Step 2: Run the Java tests and observe failure**

```bash
bash android-touch-executor/build.sh
```

Expected: missing runner types fail compilation.

- [ ] **Step 3: Implement the runner**

`AutonomousTaskRunner` owns a bounded queue, a single execution thread, a server heartbeat timer, and a result outbox uploader. It calls the existing `TouchCommandDispatcher` through a small typed adapter, persists a checkpoint before and after every visible action, and marks uncertain actions pending reconciliation instead of immediately repeating them. It never opens arbitrary URLs or executes shell commands.

- [ ] **Step 4: Start/stop with the accessibility service**

In `onServiceConnected()`, initialize the runner only after the accessibility snapshot is available and provisioning is complete. On `onDestroy()`, stop polling, flush only already-confirmed outbox entries within the timeout, and leave unfinished tasks persisted. Keep the loopback server available only behind an explicit diagnostics flag.

- [ ] **Step 5: Run Java tests and commit**

```bash
bash android-touch-executor/build.sh
 git add android-touch-executor/src android-touch-executor/test
 git commit -m "feat: execute leased work autonomously on device"
```

## Task 6: Add server coverage, priority resume, and circuit-breaker integration

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/priority_service.py`
- Modify: `src/tikpoc/fleet.py`
- Modify: `tests/test_priority_recovery.py`
- Modify: `tests/test_priority_coverage.py`
- Create: `tests/test_mobile_circuit_breaker.py`

- [ ] **Step 1: Add red tests**

Verify that a target is complete only when every configured device has a confirmed visit, a priority batch preempts only at a target boundary, the parent cursor resumes after priority completion, and two heartbeat failures stop new claims while preserving the current lease.

- [ ] **Step 2: Implement server-side state transitions**

Map mobile result receipts to existing assignment phases and action plans. Add `mobile_device_state` transitions (`healthy`, `degraded`, `paused`, `revoked`) and claim suppression when the circuit breaker is open. Keep coverage accounting based on durable confirmed visits, not task creation or pull count.

- [ ] **Step 3: Run focused tests and commit**

```bash
uv run pytest tests/test_priority_recovery.py tests/test_priority_coverage.py tests/test_mobile_circuit_breaker.py -q
 git add src/tikpoc/acquisition_db.py src/tikpoc/priority_service.py src/tikpoc/fleet.py tests/test_priority_recovery.py tests/test_priority_coverage.py tests/test_mobile_circuit_breaker.py
 git commit -m "feat: integrate mobile leases with coverage and priority recovery"
```

## Task 7: Shadow mode and VMOS provisioning adapter

**Files:**
- Create: `src/tikpoc/mobile_device_api.py`
- Modify: `src/tikpoc/vmos_cloud.py`
- Create: `tests/test_mobile_device_api.py`
- Modify: `tests/test_vmos_cloud.py`
- Create: `docs/runbooks/autonomous-apk-provisioning.md`

- [ ] **Step 1: Add fake-server contract tests**

Test registration, pull, heartbeat, result replay, and shadow-mode plan comparison without using live credentials or external devices.

- [ ] **Step 2: Implement the operator client**

Add CLI/service functions to register a device, rotate a token, inspect heartbeat state, and revoke a device. Extend the VMOS client only for install/start/restart and visible APK version checks; no per-target command is sent through VMOS OpenAPI.

- [ ] **Step 3: Document provisioning**

Document placeholders for API base URL, device ID, account ID, bootstrap token entry, APK installation, Accessibility enablement, shadow mode, and rollback. State explicitly that secrets stay in ignored local configuration.

- [ ] **Step 4: Run tests and commit**

```bash
uv run pytest tests/test_mobile_device_api.py tests/test_vmos_cloud.py -q
 git add src/tikpoc/mobile_device_api.py src/tikpoc/vmos_cloud.py tests/test_mobile_device_api.py tests/test_vmos_cloud.py docs/runbooks/autonomous-apk-provisioning.md
 git commit -m "feat: add mobile provisioning and shadow-mode tooling"
```

## Task 8: Live acceptance gates and cutover

**Files:**
- Create: `docs/runbooks/autonomous-apk-acceptance.md`
- Modify: `docs/superpowers/specs/2026-07-26-autonomous-apk-task-client-design.md`
- Modify: `AGENTS.md` only if the approved runtime policy needs a wording correction.

- [ ] **Step 1: Run complete automated verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
bash android-touch-executor/build.sh
node --test chrome-event-bridge/*.test.js
git diff --check
```

- [ ] **Step 2: Install and calibrate one VMOS device**

Use ADB only to install the signed APK, provision the API endpoint, enable Accessibility visibly, and verify the APK version. Start shadow mode and compare ten server plans with ten local plans.

- [ ] **Step 3: Execute live gates in order**

Run one device for 20 targets, 100 targets, and 30 minutes; disconnect Mac/ADB after startup and verify completion plus delayed outbox upload. Then run two devices for 30 minutes, followed by seven devices for endurance. Record measured mean, P90, completed/deferred/uncertain counts, and durable coverage in fresh SQLite evidence files.

- [ ] **Step 4: Cut over runtime transport**

Disable worker use of `DeviceSideTransport` in production configuration. Retain the transport only in diagnostics and rollback tooling. Do not report the 10,000/day target as achieved until a 24-hour run confirms durable coverage and the capacity gates.

- [ ] **Step 5: Commit runbook/checkpoint**

```bash
git add docs/runbooks/autonomous-apk-acceptance.md docs/superpowers/specs/2026-07-26-autonomous-apk-task-client-design.md AGENTS.md
git commit -m "docs: record autonomous APK acceptance and cutover"
```
