# VMOS Device-Side Touch Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and promote an on-device VMOS accessibility executor that sustains at least 500 confirmed TikTok target visits per device-hour while preserving every existing mobile identity, eligibility, action, quota, retry, and coverage invariant.

**Architecture:** A small Android helper owns only semantic UI observation and verified UI actions, serializing length-bounded JSON commands over an application-loopback socket reached through an ADB forward. Python remains authoritative for assignments and policy through a new `DeviceSideTikTokDevice`, selects the backend per device, validates every response against the worker fence and assignment phase, and persists helper-specific performance evidence beside existing stage timings. Appium remains an exclusive per-device rollback backend.

**Tech Stack:** Java 8 Android `AccessibilityService`, Android loopback sockets, hand-built Android SDK toolchain, Python 3.14, dataclasses, sockets, subprocess/ADB, SQLite, Pytest, Ruff.

---

## File Structure

**Create Android helper files:**

- `android-touch-executor/AndroidManifest.xml`: private helper application and accessibility service declaration.
- `android-touch-executor/res/xml/accessibility_service.xml`: TikTok-scoped accessibility event configuration.
- `android-touch-executor/build.sh`: deterministic Java compile, unit test, DEX, APK, sign, and verify workflow.
- `android-touch-executor/src/com/tikpoc/touch/Protocol.java`: bounded JSON request/response values and validation.
- `android-touch-executor/src/com/tikpoc/touch/SemanticSnapshot.java`: immutable normalized accessibility-node evidence.
- `android-touch-executor/src/com/tikpoc/touch/TikTokSemantics.java`: coherent profile, post, and action parsing plus unique control selection.
- `android-touch-executor/src/com/tikpoc/touch/CommandGate.java`: single-command exclusion and bounded idempotent result cache.
- `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`: command validation and semantic command execution.
- `android-touch-executor/src/com/tikpoc/touch/LoopbackCommandServer.java`: length-prefixed loopback JSON endpoint.
- `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`: snapshot/event lifecycle and helper startup.
- `android-touch-executor/test/com/tikpoc/touch/*Test.java`: dependency-free Java unit tests with synthetic trees and clocks.

**Create Python files:**

- `src/tikpoc/device_side_protocol.py`: typed command/evidence models and strict response validation.
- `src/tikpoc/device_side_transport.py`: ADB forward lifecycle and bounded socket transport.
- `src/tikpoc/device_side_device.py`: `VerifiedTikTokDevice` adapter and failure classification.
- `tests/test_device_side_protocol.py`: schema, fence, phase, identity, deadline, and evidence tests.
- `tests/test_device_side_transport.py`: framing, bounds, timeout, forwarding, and cleanup tests.
- `tests/test_device_side_device.py`: adapter behavior and exact business-contract mapping tests.

**Modify existing files:**

- `src/tikpoc/device_performance.py`: helper counters in the common performance snapshot.
- `src/tikpoc/acquisition_db.py`: persist helper performance columns with additive migration behavior.
- `src/tikpoc/acquisition_models.py`: expose helper metric fields in the stored metric value.
- `src/tikpoc/fleet.py`: per-device backend and helper-port configuration.
- `src/tikpoc/fleet_runtime.py`: exclusive Appium/device-side factories and lifecycle cleanup.
- `src/tikpoc/cli.py`: helper health and canary bootstrap commands.
- `config/fleet.example.yaml`: documented device-side configuration using placeholders.
- `tests/test_device_performance.py`, `tests/test_acquisition_db.py`, `tests/test_fleet.py`, `tests/test_fleet_runtime.py`, `tests/test_cli.py`: focused integration coverage.
- `docs/runbooks/vmos-device-side-touch.md`: install, health, canary, promotion, and rollback procedure.
- `AGENTS.md`: record the latest verified checkpoint only after live gates finish.

### Task 1: Android Protocol Values And Bounded JSON

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/Protocol.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/ProtocolTest.java`
- Create: `android-touch-executor/build.sh`

- [ ] **Step 1: Write the failing protocol test**

Create a dependency-free `ProtocolTest` whose `main` constructs this complete request and asserts required-field parsing, a `262144` byte input limit, a future monotonic deadline, and exact enum acceptance:

```java
String json = "{\"version\":1,\"command_id\":\"cmd-1\","
    + "\"command\":\"health\",\"device_id\":\"device-1\","
    + "\"account_id\":\"account-1\",\"fence_token\":7,"
    + "\"assignment_id\":19,\"phase\":\"profile_opening\","
    + "\"deadline_elapsed_ms\":9000,\"arguments\":{}}";
Protocol.Request request = Protocol.parseRequest(json, 1000);
check(request.commandId.equals("cmd-1"), "command id");
expectFailure(() -> Protocol.parseRequest(json.replace("9000", "999"), 1000),
    "deadline_expired");
expectFailure(() -> Protocol.parseRequest(repeat("x", 262145), 1000),
    "request_too_large");
```

- [ ] **Step 2: Run the Java test to verify it fails**

Run: `bash android-touch-executor/build.sh`

Expected: FAIL because `Protocol` and the helper build do not exist.

- [ ] **Step 3: Implement the minimal strict protocol**

Implement `Protocol.Request`, `Protocol.Response`, `Protocol.Error`, `parseRequest`, and `encodeResponse`. Accept only `health`, `open_profile`, `observe_profile`, `open_video`, `observe_action`, `apply_action`, and `diagnostics`; reject blank identity/fence fields, nonpositive assignment IDs, unknown phases, expired deadlines, trailing JSON, nesting deeper than 12, and payloads over 256 KiB. Responses must always contain:

```java
response.version = 1;
response.commandId = request.commandId;
response.deviceId = request.deviceId;
response.accountId = request.accountId;
response.fenceToken = request.fenceToken;
response.assignmentId = request.assignmentId;
response.phase = request.phase;
response.helperVersion = Protocol.HELPER_VERSION;
response.elapsedMs = elapsedMs;
response.packageName = packageName;
response.activityName = activityName;
response.eventSequence = eventSequence;
response.evidenceDigest = evidenceDigest;
```

Use a small repository-local JSON reader/writer in `Protocol.java`; do not add a network-fetched dependency to the hand-built APK.

- [ ] **Step 4: Build and verify the protocol test passes**

Run: `bash android-touch-executor/build.sh`

Expected: `ProtocolTest PASS`. Task 4 adds the manifest, Android service classes,
DEX packaging, signing, and APK verification after the runnable helper exists.

- [ ] **Step 5: Commit the protocol foundation**

```bash
git add android-touch-executor
git commit -m "feat: add bounded touch executor protocol"
```

### Task 2: Semantic Accessibility Snapshot And TikTok Parser

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/SemanticSnapshot.java`
- Create: `android-touch-executor/src/com/tikpoc/touch/TikTokSemantics.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/TikTokSemanticsTest.java`
- Modify: `android-touch-executor/build.sh`

- [ ] **Step 1: Write failing synthetic-tree tests**

Build synthetic nodes containing resource ID, class, text, description, bounds, visibility, clickability, selected/checked state, and children. Assert one coherent snapshot yields exact normalized username, numeric metrics, one or more stable post handles, and action state:

```java
TikTokSemantics.Profile profile = TikTokSemantics.parseProfile(snapshot);
check(profile.username.equals("target_user"), "exact username");
check(profile.following == 120 && profile.followers == 45, "metrics");
check(profile.videoCount == 2, "visible posts");
check(profile.postHandles.equals(Arrays.asList("post:0", "post:1")), "posts");
expectFailure(() -> TikTokSemantics.uniqueControl(ambiguous, "like"),
    "ambiguous_control");
```

Also assert private, suspended/missing, loading, stale-tree, hidden-node, partial-metrics, and duplicate-control fixtures produce explicit typed results rather than guessed values.

- [ ] **Step 2: Run the build to observe the missing parser failure**

Run: `bash android-touch-executor/build.sh`

Expected: FAIL with missing `SemanticSnapshot` or `TikTokSemantics` symbols.

- [ ] **Step 3: Implement normalized snapshots and coherent parsing**

`SemanticSnapshot.capture(root, eventSequence, capturedAtElapsedMs)` must copy nodes once, normalize Unicode whitespace and leading `@`, cap nodes at 4,096, and compute a SHA-256 digest from semantic fields. `TikTokSemantics` must return evidence source resource IDs and reject mixed/stale snapshots. Parse abbreviated counts (`K`, `M`, localized separators) without converting missing evidence to zero. Select controls only when exactly one visible enabled semantic match exists.

- [ ] **Step 4: Run all helper unit tests**

Run: `bash android-touch-executor/build.sh`

Expected: `ProtocolTest PASS` and `TikTokSemanticsTest PASS`.

- [ ] **Step 5: Commit semantic observation**

```bash
git add android-touch-executor
git commit -m "feat: parse TikTok accessibility evidence"
```

### Task 3: Exclusive And Idempotent Command Gate

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/CommandGate.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/CommandGateTest.java`
- Modify: `android-touch-executor/build.sh`

- [ ] **Step 1: Write failing concurrency and replay tests**

Use two threads and a blocking callable. Assert the first command runs once, its concurrent duplicate waits and receives the identical stored result, another command receives `busy`, completed results are capped at 32 entries/256 KiB total, and expired entries are evicted after 60 seconds:

```java
Protocol.Response first = gate.execute(request("cmd-1"), blockingHandler);
Protocol.Response replay = gate.execute(request("cmd-1"), failIfCalledHandler);
check(first.encoded.equals(replay.encoded), "idempotent replay");
check(invocations.get() == 1, "single execution");
```

- [ ] **Step 2: Run the helper build and verify failure**

Run: `bash android-touch-executor/build.sh`

Expected: FAIL because `CommandGate` is missing.

- [ ] **Step 3: Implement the command gate**

Synchronize admission around one `inFlightCommandId`. Return cached bytes for completed duplicate IDs, share the in-flight future for an exact duplicate, return a typed `busy` response for a different ID, and cache success and terminal error responses only after handler completion. Never cache a truncated response.

- [ ] **Step 4: Run helper tests repeatedly**

Run: `for i in {1..20}; do bash android-touch-executor/build.sh >/tmp/tikpoc-helper-test.log || exit 1; done`

Expected: exit 0 with no concurrency flakes.

- [ ] **Step 5: Commit command ownership**

```bash
git add android-touch-executor
git commit -m "feat: serialize touch helper commands"
```

### Task 4: Accessibility Service, Dispatcher, And Loopback Server

**Files:**
- Create: `android-touch-executor/AndroidManifest.xml`
- Create: `android-touch-executor/res/xml/accessibility_service.xml`
- Create: `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`
- Create: `android-touch-executor/src/com/tikpoc/touch/LoopbackCommandServer.java`
- Create: `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/TouchCommandDispatcherTest.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/LoopbackCommandServerTest.java`
- Modify: `android-touch-executor/build.sh`

- [ ] **Step 1: Write failing dispatcher state-machine tests**

Use fake clock, fake snapshot source, and fake semantic actuator. Verify `health` is read-only; `open_profile` waits for a changed exact identity; profile observation combines identity/metrics/posts from one snapshot; `open_video` verifies video controls; `apply_action` records before/after state and never clicks twice; diagnostics excludes all node text:

```java
Protocol.Response response = dispatcher.dispatch(apply("like"));
check(actuator.clickCount == 1, "one click");
check(response.evidence.get("before").equals("off"), "before state");
check(response.evidence.get("after").equals("on"), "after state");
check(response.evidence.get("control_resource_id").equals("like_button"),
    "unique control");
```

Assert a click followed by missing final evidence returns `uncertain`; absent, hidden, stale, and ambiguous controls return typed non-clicking failures.

- [ ] **Step 2: Write failing loopback framing tests**

Bind a random local port, send a four-byte big-endian length plus UTF-8 JSON, and assert one framed response. Verify non-loopback bind addresses, zero/oversized lengths, partial payloads, two concurrent different commands, and idle read timeout are rejected.

- [ ] **Step 3: Run helper tests to observe failures**

Run: `bash android-touch-executor/build.sh`

Expected: FAIL with missing dispatcher/server/service classes.

- [ ] **Step 4: Implement the service and bounded commands**

Declare only `android.permission.INTERNET`; configure the exported accessibility service with `android.permission.BIND_ACCESSIBILITY_SERVICE`, package `com.zhiliaoapp.musically`, window-content retrieval, and relevant window/content/click/selection events. Bind `ServerSocket` to `InetAddress.getLoopbackAddress()` only. Route commands through `CommandGate`; use `AccessibilityNodeInfo.performAction(ACTION_CLICK)` only on a unique current node; use an `ACTION_VIEW` TikTok profile URI for `open_profile`; wait on event sequence changes until the monotonic deadline.

- [ ] **Step 5: Build, test, sign, and inspect the APK**

Run: `bash android-touch-executor/build.sh && $ANDROID_HOME/build-tools/37.0.0/aapt2 dump permissions android-touch-executor/build/touch-executor.apk`

Expected: all helper tests pass; APK verifies; permissions contain `INTERNET` and exclude storage, contacts, accounts, overlay, and VPN permissions.

- [ ] **Step 6: Commit the runnable helper**

```bash
git add android-touch-executor
git commit -m "feat: run semantic commands on Android"
```

### Task 5: Python Protocol And Response Validation

**Files:**
- Create: `src/tikpoc/device_side_protocol.py`
- Create: `tests/test_device_side_protocol.py`

- [ ] **Step 1: Write failing typed-protocol tests**

```python
def test_response_requires_matching_execution_context():
    context = CommandContext(
        command_id="cmd-1", device_id="device-1", account_id="account-1",
        fence_token=7, assignment_id=19, phase="profile_opening",
        deadline_monotonic_ms=9_000,
    )
    payload = valid_health_payload(command_id="other")
    with pytest.raises(DeviceSideProtocolError, match="command_id_mismatch"):
        parse_response(payload, context=context, now_monotonic_ms=2_000)
```

Cover unsupported version, fence/device/account/assignment/phase mismatch, deadline expiry, wrong TikTok package, incomplete evidence, invalid numeric metrics, duplicate post handles, malformed before/after action state, oversized diagnostics, and target identity mismatch.

- [ ] **Step 2: Run the focused tests and verify import failure**

Run: `uv run pytest tests/test_device_side_protocol.py -q`

Expected: FAIL because `tikpoc.device_side_protocol` does not exist.

- [ ] **Step 3: Implement immutable protocol dataclasses**

Define `CommandContext`, `HelperHealth`, `ProfileEvidence`, `VideoEvidence`, `ActionEvidence`, `HelperDiagnostics`, `HelperResponse`, and `DeviceSideProtocolError`. `build_request()` emits protocol version 1 and the complete execution context. `parse_response()` rejects any mismatch before constructing evidence and requires helper versions in `SUPPORTED_HELPER_VERSIONS = {"1.0.0"}`.

- [ ] **Step 4: Run focused protocol tests**

Run: `uv run pytest tests/test_device_side_protocol.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit host contract validation**

```bash
git add src/tikpoc/device_side_protocol.py tests/test_device_side_protocol.py
git commit -m "feat: validate device-side helper responses"
```

### Task 6: ADB Forward And Bounded Socket Transport

**Files:**
- Create: `src/tikpoc/device_side_transport.py`
- Create: `tests/test_device_side_transport.py`

- [ ] **Step 1: Write failing transport tests**

Use a fake `subprocess.run` and local socket server. Assert startup executes the exact serial-scoped forward, health uses one framed request/response, timeouts and EOF are `DeviceSideTransportError`, responses over 256 KiB are rejected, close removes only its own forward, and a second transport cannot claim the same `(adb_endpoint, host_port)` registry key:

```python
transport.start()
assert commands == [["adb", "-s", "ADB_ENDPOINT", "forward",
                    "tcp:47101", "tcp:47101"]]
transport.close()
assert commands[-1] == ["adb", "-s", "ADB_ENDPOINT", "forward", "--remove",
                        "tcp:47101"]
```

- [ ] **Step 2: Run focused tests and observe failure**

Run: `uv run pytest tests/test_device_side_transport.py -q`

Expected: FAIL because the transport module is missing.

- [ ] **Step 3: Implement explicit transport lifecycle**

Implement `DeviceSideTransport(adb_endpoint, host_port, device_port, timeout_seconds=10, max_payload_bytes=262_144)`. Use `adb -s`, `subprocess.run(..., check=True, capture_output=True, text=True, timeout=10)`, `socket.create_connection(("127.0.0.1", host_port))`, four-byte big-endian lengths, `sendall`, and exact-length reads. Redact payloads and subprocess output from exception text.

- [ ] **Step 4: Run focused transport tests**

Run: `uv run pytest tests/test_device_side_transport.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit transport ownership**

```bash
git add src/tikpoc/device_side_transport.py tests/test_device_side_transport.py
git commit -m "feat: transport device-side touch commands"
```

### Task 7: `DeviceSideTikTokDevice` Adapter

**Files:**
- Create: `src/tikpoc/device_side_device.py`
- Create: `tests/test_device_side_device.py`
- Modify: `src/tikpoc/mobile_worker.py`
- Modify: `tests/test_mobile_worker.py`

- [ ] **Step 1: Write failing adapter contract tests**

Drive a fake transport through the exact `VerifiedTikTokDevice` call order. Assert `ensure_ready`, route, exact identity, coherent profile observation, stable post keys, video confirmation, all four outcomes, one read-only reconciliation after uncertain, diagnostics, recovery, and performance snapshots:

```python
device.open_target(target)
device.confirm_profile_identity(target)
observation = device.read_profile_observation()
assert observation.following == 120
assert observation.followers == 45
assert observation.video_count == 2
device.open_and_confirm_video("post:1")
assert device.execute_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED
```

Assert the adapter sends the current assignment ID/phase/fence in every command and maps transport/helper/version failures to fatal `DeviceSideUnavailable`, stale/ambiguous evidence to deferred `DeviceSideEvidenceError`, and possibly-applied action without final evidence to `ActionResult.UNCERTAIN`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run pytest tests/test_device_side_device.py tests/test_mobile_worker.py -q`

Expected: FAIL because the adapter and assignment-context hook are missing.

- [ ] **Step 3: Add explicit assignment context to the device protocol**

Add this method to `VerifiedTikTokDevice` and call it immediately before `_run_claimed`:

```python
def bind_assignment(
    self, assignment_id: int, phase: AssignmentPhase,
    *, account_id: str, fence_token: int,
) -> None: ...
```

Update `FencedVerifiedDevice`, Appium, and test fakes with a no-op implementation. Before each phase-specific device call, update the bound phase so helper responses are checked against durable worker state.

- [ ] **Step 4: Implement the adapter without policy duplication**

`DeviceSideTikTokDevice` stores only current target/evidence/context and delegates commands. Convert helper profile evidence to existing `ProfileObservation` and `ProfileAccessState`; do not evaluate `following > followers`, choose a post/action, reserve quotas, retry clicks, or create plans. `open_target` sends the stable profile URL; `confirm_profile_identity` requires normalized exact equality; `execute_outcome(TRACE)` returns confirmed without `apply_action`; reconciliation calls only `observe_action`.

- [ ] **Step 5: Run adapter and worker tests**

Run: `uv run pytest tests/test_device_side_device.py tests/test_mobile_worker.py tests/test_appium_device.py -q`

Expected: all tests pass with existing worker behavior unchanged.

- [ ] **Step 6: Commit the verified adapter**

```bash
git add src/tikpoc/device_side_device.py src/tikpoc/mobile_worker.py \
  tests/test_device_side_device.py tests/test_mobile_worker.py tests/test_appium_device.py
git commit -m "feat: adapt device-side touch evidence"
```

### Task 8: Helper Performance Persistence

**Files:**
- Modify: `src/tikpoc/device_performance.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/mobile_worker.py`
- Modify: `tests/test_device_performance.py`
- Modify: `tests/test_acquisition_db.py`
- Modify: `tests/test_mobile_worker.py`

- [ ] **Step 1: Write failing snapshot and migration tests**

Extend snapshot subtraction and database assertions with:

```python
assert delta.helper_command_count == 2
assert delta.helper_processing_ms == 31
assert delta.host_round_trip_ms == 44
assert delta.tree_age_ms == 7
assert delta.event_wait_ms == 18
assert delta.fallback_count == 1
assert stored.fallback_reason == "stale_tree"
```

Open a database created with the prior schema and assert repository initialization adds all columns without dropping existing rows.

- [ ] **Step 2: Run focused tests and observe missing-field failures**

Run: `uv run pytest tests/test_device_performance.py tests/test_acquisition_db.py tests/test_mobile_worker.py -q`

Expected: FAIL on missing helper metric fields.

- [ ] **Step 3: Add additive metrics and persistence**

Extend `DevicePerformanceSnapshot` with cumulative helper counters. Extend `assignment_command_metrics` using repository migration helpers and persist `helper_command_count`, `helper_processing_ms`, `host_round_trip_ms`, `tree_age_ms`, `event_wait_ms`, `fallback_count`, and nullable bounded `fallback_reason`. Keep existing Appium fields and queries backward-compatible. Aggregate metadata only; never store target IDs or visible text in metric rows.

- [ ] **Step 4: Run focused and schema tests**

Run: `uv run pytest tests/test_device_performance.py tests/test_acquisition_db.py tests/test_mobile_worker.py tests/test_supabase_migrations.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit performance evidence**

```bash
git add src/tikpoc/device_performance.py src/tikpoc/acquisition_models.py \
  src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py \
  tests/test_device_performance.py tests/test_acquisition_db.py tests/test_mobile_worker.py
git commit -m "feat: persist touch helper performance"
```

### Task 9: Per-Device Backend Configuration And Exclusive Runtime

**Files:**
- Modify: `src/tikpoc/fleet.py`
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `config/fleet.example.yaml`
- Modify: `tests/test_fleet.py`
- Modify: `tests/test_fleet_runtime.py`

- [ ] **Step 1: Write failing configuration tests**

Parse one Appium and one device-side entry:

```yaml
devices:
  - device_id: device-1
    account_id: account-1
    backend: device-side
    adb_endpoint: 127.0.0.1:5555
    helper_host_port: 47101
    helper_device_port: 47101
    order_seed: seed-1
```

Assert `backend` accepts only `appium` or `device-side`; Appium requires `appium_url`; device-side requires unique helper host ports; port collisions and missing fields fail before a worker starts.

- [ ] **Step 2: Write failing runtime factory tests**

Assert device-side startup creates/starts only `DeviceSideTransport` and `DeviceSideTikTokDevice`, Appium startup creates only its driver/router, cleanup always closes the chosen backend, and neither path initializes the other backend for the same fence.

- [ ] **Step 3: Run focused tests and observe failures**

Run: `uv run pytest tests/test_fleet.py tests/test_fleet_runtime.py -q`

Expected: FAIL on unknown backend fields and missing runtime factory.

- [ ] **Step 4: Implement backend-specific configuration and lifecycle**

Add `backend: str = "appium"`, `helper_host_port: int | None`, and `helper_device_port: int | None` to `FleetDevice`. Refactor `run_device_worker` to call a `create_verified_device(device, fence, database_path)` context manager. The Appium branch keeps `MeasuredAppiumDriver` and `AdbProfileRouter`; the device-side branch starts transport, verifies helper health/version/package, creates the adapter, and closes/removes the forward in `finally`.

- [ ] **Step 5: Run fleet and worker regression**

Run: `uv run pytest tests/test_fleet.py tests/test_fleet_runtime.py tests/test_mobile_worker.py tests/test_runner.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit selectable exclusive backends**

```bash
git add src/tikpoc/fleet.py src/tikpoc/fleet_runtime.py config/fleet.example.yaml \
  tests/test_fleet.py tests/test_fleet_runtime.py
git commit -m "feat: select device-side fleet backends"
```

### Task 10: VMOS Bootstrap, Health CLI, And Runbook

**Files:**
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_cli.py`
- Create: `docs/runbooks/vmos-device-side-touch.md`

- [ ] **Step 1: Write failing CLI tests**

Assert `tikpoc helper-health --fleet config/fleet.yaml --device-id device-1` installs no packages and prints redacted JSON containing device ID, helper version, service enabled, TikTok foreground, surface, occupancy, and latency. Assert `tikpoc helper-bootstrap` runs serial-scoped install, service-state query, forward, health, and cleanup; reject non-device-side entries.

- [ ] **Step 2: Run CLI tests and observe missing commands**

Run: `uv run pytest tests/test_cli.py -q`

Expected: FAIL because `helper-health` and `helper-bootstrap` are unregistered.

- [ ] **Step 3: Implement explicit health/bootstrap commands**

Use argument arrays only:

```python
["adb", "-s", device.adb_endpoint, "install", "-r", str(apk_path)]
["adb", "-s", device.adb_endpoint, "shell", "settings", "get", "secure",
 "enabled_accessibility_services"]
```

The command reports that visible user enablement is required when the component is absent; it does not alter unrelated accessibility settings. Never print fleet secrets, proxy data, target content, raw diagnostics, or authenticated state.

- [ ] **Step 4: Write the operational runbook**

Document exact build, install, visible accessibility enablement, ADB forward, health, 20/100/30-minute gates, audit queries, stop, forward removal, service disablement, backend config restoration, and resume from the same durable round. Include the rejection rules: any correctness regression, `<400/hour` at 20, or `<500/hour` at 100 returns the canary to Appium.

- [ ] **Step 5: Run CLI tests and inspect help**

Run: `uv run pytest tests/test_cli.py -q && uv run tikpoc --help`

Expected: tests pass and both helper commands appear without secret-bearing defaults.

- [ ] **Step 6: Commit bootstrap operations**

```bash
git add src/tikpoc/cli.py tests/test_cli.py docs/runbooks/vmos-device-side-touch.md
git commit -m "feat: bootstrap VMOS touch helper"
```

### Task 11: Full Automated Verification And APK Inspection

**Files:**
- Modify only files required to fix failures found by these checks.

- [ ] **Step 1: Run the helper build twice from clean output**

Run: `rm -rf android-touch-executor/build && bash android-touch-executor/build.sh && rm -rf android-touch-executor/build && bash android-touch-executor/build.sh`

Expected: all Java tests pass twice; signed APK verifies twice.

- [ ] **Step 2: Run the complete Python suite**

Run: `uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run repository-wide static verification**

Run: `uv tool run ruff check src tests && uv tool run ruff format --check src tests && node --test chrome-event-bridge/*.test.js && bash android-event-bridge/build.sh && git diff --check`

Expected: every command exits 0. Browser automation remains behaviorally untouched and automatic follow-back/replies remain disabled.

- [ ] **Step 4: Inspect committed content for secrets and generated APKs**

Run: `git status --short && git ls-files | rg '(\.apk$|debug\.keystore$|\.db$|\.env)' || true && git diff --cached --check`

Expected: helper build artifacts, signing keys, databases, local configuration, target data, and credentials are absent from tracked changes.

- [ ] **Step 5: Commit any verification-only corrections**

```bash
git add android-touch-executor src/tikpoc/device_side_protocol.py \
  src/tikpoc/device_side_transport.py src/tikpoc/device_side_device.py \
  src/tikpoc/device_performance.py src/tikpoc/acquisition_models.py \
  src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py src/tikpoc/fleet.py \
  src/tikpoc/fleet_runtime.py src/tikpoc/cli.py config/fleet.example.yaml \
  tests/test_device_side_protocol.py tests/test_device_side_transport.py \
  tests/test_device_side_device.py tests/test_device_performance.py \
  tests/test_acquisition_db.py tests/test_mobile_worker.py tests/test_appium_device.py \
  tests/test_fleet.py tests/test_fleet_runtime.py tests/test_cli.py \
  docs/runbooks/vmos-device-side-touch.md
git commit -m "fix: satisfy touch executor verification"
```

Skip this commit when no correction was needed.

### Task 12: Single-Device VMOS Health Acceptance

**Files:**
- Create locally, do not commit: `config/vmos-device-side.local.yaml`
- Create locally, do not commit: `build/vmos-device-side-health.json`
- Modify: `docs/runbooks/vmos-device-side-touch.md` only if the observed setup differs from the tested commands.

- [ ] **Step 1: Build and install the repository APK on the selected canary**

Run: `bash android-touch-executor/build.sh && uv run tikpoc helper-bootstrap --fleet config/vmos-device-side.local.yaml --device-id VMOS_CANARY --apk android-touch-executor/build/touch-executor.apk`

Expected: install succeeds; the command either reports service healthy or identifies the exact visible accessibility enablement step.

- [ ] **Step 2: Enable the helper visibly and re-run health**

Enable `TikPoc Touch Executor` in the VMOS Accessibility settings, keep TikTok foreground, then run:

`uv run tikpoc helper-health --fleet config/vmos-device-side.local.yaml --device-id VMOS_CANARY > build/vmos-device-side-health.json`

Expected: `service_enabled=true`, `tiktok_foreground=true`, supported helper version, `busy=false`, bounded latency, and no target text or secrets in output.

- [ ] **Step 3: Verify exclusivity and rollback before target actions**

Stop the device-side health process, confirm its ADB forward is removed, start the same device with the Appium backend at the unchanged round checkpoint, stop it, then restore device-side configuration. Do not allow both sessions to overlap.

- [ ] **Step 4: Commit runbook corrections if observed behavior required them**

```bash
git add docs/runbooks/vmos-device-side-touch.md
git commit -m "docs: calibrate VMOS touch helper bootstrap"
```

Skip this commit if the documented procedure matched the device.

### Task 13: 20-Target Correctness And Minimum-Rate Gate

**Files:**
- Create locally, do not commit: `build/vmos-device-side-20.db`
- Create locally, do not commit: `build/vmos-device-side-20-report.json`
- Modify: `docs/runbooks/vmos-device-side-touch.md` with redacted measured results.

- [ ] **Step 1: Create a fresh 20-target canary round**

Use the existing canary import/round CLI against a fresh database, the approved target fixture, one device/account, and device-side backend. Record the generated round ID in the ignored report file; do not reuse the Appium canary database.

- [ ] **Step 2: Run exactly the canary round and preserve the database**

Start the fleet for that round, wait for operational completion or a correctness failure, and stop the worker. Do not modify eligibility (`following > followers AND video_count >= 1`), immutable outcome plans, rolling quotas, or uncertain reconciliation behavior to improve the result.

- [ ] **Step 3: Audit correctness from durable evidence**

Run repository queries/tests that assert `20/20` confirmed visits, exact identity on every visit, zero duplicate assignments/plans/attempt indexes, at most one action click per attempt, repost resulting-state evidence, one read-only reconciliation for uncertain, and no due interaction converted to trace.

Expected: every correctness assertion passes.

- [ ] **Step 4: Calculate measured throughput and stage/helper distributions**

Use first claimed timestamp to final completion timestamp. Report confirmed/hour, 20-hour projection, mean/P90 total target duration, stage mean/P90, helper processing, host round trip, tree age, event wait, and fallback reasons.

Expected promotion gate: at least `400 confirmed/hour`. A lower value rejects the backend for further live expansion until code changes pass Tasks 11-13 again.

- [ ] **Step 5: Record redacted evidence and commit**

```bash
git add docs/runbooks/vmos-device-side-touch.md
git commit -m "docs: record device-side 20-target gate"
```

### Task 14: Representative 100-Target Gate

**Files:**
- Create locally, do not commit: `build/vmos-device-side-100.db`
- Create locally, do not commit: `build/vmos-device-side-100-report.json`
- Modify: `docs/runbooks/vmos-device-side-touch.md`

- [ ] **Step 1: Create and execute a fresh representative 100-target round**

Use the same canary account/device and backend but a new database and round. Run through operational completion while retaining all failures and performance records.

- [ ] **Step 2: Run the full correctness audit**

Require `100/100` confirmed coverage, exact identity evidence, immutable plans, valid quota reservations/results, no duplicates, verified interactions, and existing explicit terminal/deferred classifications. Inspect a stratified sample containing every outcome and every encountered failure category.

- [ ] **Step 3: Calculate measured capacity**

Report measured confirmed/hour separately from projected 20-hour capacity and include mean/P90 target and helper metrics.

Expected promotion gate: at least `500 confirmed/hour`. Below `500/hour`, retain the failed report, restore Appium, and return to the measured slow stages rather than expanding device count.

- [ ] **Step 4: Record redacted evidence and commit**

```bash
git add docs/runbooks/vmos-device-side-touch.md
git commit -m "docs: record device-side 100-target gate"
```

### Task 15: 30-Minute Promotion Canary And Fleet Expansion

**Files:**
- Create locally, do not commit: `build/vmos-device-side-30m.db`
- Create locally, do not commit: `build/vmos-device-side-30m-report.json`
- Modify: `docs/runbooks/vmos-device-side-touch.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Run a fresh uninterrupted 30-minute canary**

Use a sufficiently large fresh round so the worker never idles. Measure only the stable interval after helper health and TikTok readiness pass. Preserve the database and report wall-clock start/end.

- [ ] **Step 2: Apply all promotion gates together**

Require at least `500 confirmed/hour`, projected `>=10,000` in 20 productive hours, per-target mean `<6.5s`, P90 `<8.64s`, exact identity/action audits, valid quotas, and zero duplicate coverage. Report any shortfall directly; short peaks and extrapolated partial samples do not pass.

- [ ] **Step 3: Expand to two devices only after single-device promotion**

Configure unique ADB endpoints, helper host ports, device/account identities, and fence owners. Run the same shared logical batch and verify both devices preserve individual rate plus `2/2` durable coverage without port collisions or shared command occupancy.

- [ ] **Step 4: Expand to the configured active account fleet**

Add devices incrementally, preserving one account per device and one helper transport per fence. Verify each device rate independently and fleet coverage as `N/N`; do not report the seven-account `70,000` visit capacity unless seven active accounts pass the same sustained evidence.

- [ ] **Step 5: Exercise rollback from a durable checkpoint**

Stop device-side workers, verify forwards removed and helper services idle/disabled, change each affected device to Appium, and resume the same round without new plans, duplicate action attempts, or lost confirmed visits. Restore the promoted backend only after the rollback audit passes.

- [ ] **Step 6: Update the checkpoint and run final verification**

Record measured single-device and fleet results, remaining limitations, exact latest commit, and rollback status in `AGENTS.md`. Run:

`uv run pytest -q && uv tool run ruff check src tests && uv tool run ruff format --check src tests && bash android-touch-executor/build.sh && node --test chrome-event-bridge/*.test.js && bash android-event-bridge/build.sh && git diff --check`

Expected: all checks pass and the checkpoint distinguishes measured throughput from projections.

- [ ] **Step 7: Commit and push the accepted checkpoint**

```bash
git add AGENTS.md docs/runbooks/vmos-device-side-touch.md
git commit -m "docs: promote VMOS device-side touch executor"
git push origin feat/web-lead-conversion
```

## Plan Self-Review

- Spec coverage: Tasks 1-4 cover protocol, coherent snapshots, semantic controls, exclusion, idempotency, loopback binding, service lifecycle, and bounded diagnostics. Tasks 5-7 cover host validation, fence/phase/deadline/identity enforcement, transport, failure classification, and the unchanged `VerifiedTikTokDevice` business boundary. Tasks 8-10 cover required metrics, fleet selection, helper bootstrap, secret handling, and rollback operations. Tasks 11-15 cover automated regression and every stated live promotion gate in order.
- Business invariants: Task 7 explicitly leaves eligibility, plans, quota, action choice, terminal handling, and reconciliation in Python. Tasks 13-15 independently audit exact identity, one selected action, repost resulting state, one read-only uncertain reconciliation, no interaction-to-trace conversion, and durable coverage.
- Type consistency: Every command uses `CommandContext(command_id, device_id, account_id, fence_token, assignment_id, phase, deadline_monotonic_ms)`. Android responses echo the same context. `DeviceSideTikTokDevice` implements the extended `VerifiedTikTokDevice`, and both backends pass through `FencedVerifiedDevice`.
- Operational isolation: Helper ports are unique, sockets bind to device loopback, ADB forwarding is serial-scoped and removed in `finally`, backend ownership is exclusive, and the APK contains no account, target, proxy, or VMOS secret.
- Placeholder scan: Uppercase names in shell examples (`ADB_ENDPOINT`, `VMOS_CANARY`) are explicit local fixture values supplied through ignored configuration; implementation steps contain concrete APIs, limits, commands, expected failures, and acceptance results. There are no deferred implementation markers.
