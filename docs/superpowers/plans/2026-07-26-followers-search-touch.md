# Followers Search Touch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect followers JSONL batches to TikPoc and add an immutable, exact-match TikTok in-app search navigation mode to the autonomous Android APK.

**Architecture:** Persist `navigation_mode` on every exposure round and propagate it through priority batches and mobile task envelopes. The Android executor delegates navigation to a mode-aware UI adapter; search mode opens TikTok search, enters the exact username, selects only a unique exact user row, and verifies the destination username before normal profile/action processing. Search failures are explicit terminal skips without confirmed coverage and never fall back to Deeplink.

**Tech Stack:** Python 3.14, SQLite, FastAPI/Pydantic, Java 8 Android AccessibilityService, shell/Java/Python tests, Git.

---

## File map

- `src/tikpoc/navigation.py`: navigation mode enum and validation.
- `src/tikpoc/acquisition_db.py`: schema migration, immutable round mode, task envelope projection.
- `src/tikpoc/acquisition_models.py`: round/priority models expose navigation mode.
- `src/tikpoc/priority_importer.py`: shared followers JSONL source contract.
- `src/tikpoc/priority_service.py`, `src/tikpoc/cli.py`: source ID and search mode CLI handoff.
- `src/tikpoc/device_api.py`: mobile envelope carries navigation mode.
- `android-touch-executor/.../AutonomousTaskExecutor.java`: mode-aware navigation invocation.
- `android-touch-executor/.../AccessibilityUiAdapter.java`: Android command bridge.
- `android-touch-executor/.../TouchCommandDispatcher.java`: search command dispatch.
- `android-touch-executor/.../TikTokSearchSemantics.java`: exact search-result and search-surface semantics.
- `android-touch-executor/.../TikPocAccessibilityService.java`: search UI orchestration.
- `/Users/chenyuqi/Desktop/followers/TIKPOC_HANDOFF.md`: collector-AI contract.

### Task 1: Versioned navigation domain

**Files:**
- Create: `src/tikpoc/navigation.py`
- Create: `tests/test_navigation.py`

- [ ] Write tests asserting `NavigationMode.parse("search")` and `parse("deeplink")` work while unknown/blank modes raise `ValueError`.
- [ ] Run `uv run pytest tests/test_navigation.py -q`; expect import failure.
- [ ] Implement a string enum with strict parsing and `DEEPLINK`/`SEARCH` values.
- [ ] Run the focused test and Ruff checks.
- [ ] Commit `feat: define immutable mobile navigation modes`.

### Task 2: Persist navigation mode on rounds and priority batches

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `src/tikpoc/rounds.py`
- Modify: `tests/test_rounds.py`
- Modify: `tests/test_priority_batches.py`

- [ ] Add failing migration tests proving legacy rounds become `deeplink` and new search rounds retain `search` after reopen.
- [ ] Add failing priority tests proving idempotency includes mode and a replay with a different mode conflicts.
- [ ] Run focused tests and confirm missing-column/signature failures.
- [ ] Add `navigation_mode TEXT NOT NULL DEFAULT 'deeplink' CHECK (...)` to `exposure_rounds`; expose it in round and priority models.
- [ ] Thread a validated mode through `create_exposure_round` and `create_priority_batch`; include it in immutable digest/comparison.
- [ ] Run round, migration, priority scheduler and priority batch suites.
- [ ] Commit `feat: persist round navigation mode`.

### Task 3: Unify followers JSONL and CLI handoff

**Files:**
- Modify: `src/tikpoc/priority_importer.py`
- Modify: `src/tikpoc/priority_service.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_priority_importer.py`
- Modify: `tests/test_priority_service.py`
- Modify: `tests/test_priority_cli.py`

- [ ] Add failing JSONL tests for required `source_type`/`source_id`, optional legacy `source_live_id`, stable-ID dedupe, and source mismatch rejection.
- [ ] Add failing CLI tests for `--source-id SOURCE --navigation-mode search --json` and compatibility with `--source-live`.
- [ ] Run focused tests and verify argument/model failures.
- [ ] Extend the importer contract without reading followers internals; normalize source fields and keep atomic file-change checks.
- [ ] Pass search mode into priority batch creation and return it in machine JSON/status.
- [ ] Keep old `--source-live` as one release compatibility alias; reject conflicting aliases.
- [ ] Run all priority importer/service/CLI/scheduler tests and Ruff.
- [ ] Commit `feat: accept followers search batches`.

### Task 4: Propagate navigation mode through mobile API

**Files:**
- Modify: `src/tikpoc/device_api.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/api.py`
- Modify: `tests/test_mobile_device_api.py`
- Modify: `tests/test_acquisition_db.py`

- [ ] Add a failing pull test asserting the JSON task contains `"navigation_mode":"search"` for a search round.
- [ ] Add a failing retry test proving the mode remains unchanged across lease expiration and session replacement.
- [ ] Run focused tests and verify the field is absent.
- [ ] Add `navigation_mode` to `MobileTaskEnvelope` and populate it from the assignment round in the same read transaction.
- [ ] Serialize it in `/api/mobile/pull`; reject invalid stored modes rather than silently defaulting.
- [ ] Run mobile API, autonomous server and acquisition DB tests.
- [ ] Commit `feat: deliver navigation mode to mobile workers`.

### Task 5: Parse and retain search mode in the Android queue

**Files:**
- Modify: `android-touch-executor/src/com/tikpoc/touch/DeviceApiClient.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/DeviceTaskStore.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/DeviceApiClientTest.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/DeviceTaskStoreTest.java`

- [ ] Add failing Java tests proving a pulled search task retains mode after enqueue, checkpoint and process-store reload.
- [ ] Run `bash android-touch-executor/build.sh`; expect assertion/constructor failure.
- [ ] Add strict `navigationMode` parsing with only `search` and `deeplink`; persist it in task payload/state without DB schema duplication.
- [ ] Re-run Android tests and build.
- [ ] Commit `feat: retain mobile search navigation mode`.

### Task 6: Exact TikTok search semantics

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/TikTokSearchSemantics.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/TikTokSearchSemanticsTest.java`

- [ ] Add failing pure-Java tests for Unicode direction controls, NFKC/case normalization, unique exact match, no match and ambiguous exact match.
- [ ] Run the Android build and confirm the missing class failure.
- [ ] Implement pure tree-semantic selection returning a clickable ancestor only for one exact visible username; never inspect nickname/avatar as identity.
- [ ] Re-run Java tests and commit `feat: identify exact TikTok search results`.

### Task 7: Android in-app search state machine

**Files:**
- Modify: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskExecutor.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/AccessibilityUiAdapter.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/Protocol.java`
- Modify: corresponding Java tests.

- [ ] Add failing executor tests asserting search mode invokes search navigation, no-match returns `skipped/search_no_exact_match`, and Deeplink is never called as fallback.
- [ ] Add failing dispatcher/service tests for stable-entry recovery, search field clear/input, Users tab selection, unique result click and destination identity verification.
- [ ] Run Android build and confirm failures.
- [ ] Extend `Ui.openProfile(target)` to dispatch `open_profile_search` for search mode and existing `open_profile` for Deeplink.
- [ ] Implement event-driven waits with explicit codes: `search_no_exact_match`, `search_ambiguous_exact_match`, `search_surface_timeout`, `profile_identity_mismatch`.
- [ ] Ensure direct text entry handles letters, digits, dot and underscore under the configured Latin IME; clear the complete prior query before every target.
- [ ] Map exact-search resolution failures to terminal skipped results without confirmed evidence; keep infrastructure/timeouts deferred.
- [ ] Run every Android unit test and build; commit `feat: navigate TikTok profiles through exact search`.

### Task 8: Search composite policy and funnel

**Files:**
- Modify: `src/tikpoc/rules.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/runtime_metadata.py`
- Modify: `tests/test_rules.py`
- Modify: `tests/test_acquisition_db.py`
- Modify: `tests/test_runtime_metadata.py`

- [ ] Add failing policy tests for a versioned search policy: exact confirmed profile, `video_count >= 1`, exactly one quota-valid interaction; zero posts remains trace-only.
- [ ] Add failing result tests proving search unresolved never increments coverage and exact profile observation does.
- [ ] Implement the new policy version without rewriting historical plans; store the chosen version with each plan.
- [ ] Add search-stage counters/error breakdowns to the existing operations read model.
- [ ] Run rules, acquisition DB, API and runtime metadata tests; commit `feat: measure search composite touches`.

### Task 9: Collector AI handoff documentation

**Files:**
- Create: `/Users/chenyuqi/Desktop/followers/TIKPOC_HANDOFF.md`
- Modify: `README.md`
- Modify: `docs/priority-live-batch-cli.md`
- Modify: `docs/tikpoc-business-logic.md`
- Modify: `AGENTS.md`

- [ ] Document the exact JSONL schema, atomic rename sequence, CLI examples, exit codes, idempotent replay, and prohibited direct DB access.
- [ ] Update TikPoc documentation to link the single contract and define exact-search/no-fallback behavior.
- [ ] Update AGENTS with the new versioned search policy and keep historical Deeplink semantics explicit.
- [ ] Scan documents for conflicting eligibility/navigation statements and correct only current source-of-truth pages.
- [ ] Commit TikPoc docs; preserve followers `.env` and generated exports.

### Task 10: Full verification and paused canary handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-07-26-followers-search-touch-design.md` (status only)
- Create: `docs/runbooks/search-touch-canary.md`

- [ ] Run `uv run pytest -q`.
- [ ] Run Chrome Node tests, operator-console unit/build/e2e, developer-site tests, and both Android builds.
- [ ] Run Ruff/format on touched Python files and `git diff --check`.
- [ ] Build the APK and record checksum, commit, protocol and policy version in the runbook.
- [ ] Confirm the current acquisition round remains paused and no live task was resumed.
- [ ] Document the later 10/50/100/overnight gate commands and explicit stop conditions; do not start the overnight run without the promised account and user instruction.
- [ ] Commit `docs: publish exact search canary runbook` and push the branch.
