# Seven-Account Acquisition Delivery Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved seven-account acquisition system in dependency order while preserving completed browser work and proving every production claim with visible-state and endurance evidence.

**Architecture:** Three implementation plans share one SQLite state source: reliable mobile acquisition rounds, the Chrome lead-conversion path, and the operator console. Each plan produces independently testable checkpoints. Live gates progress from controlled selectors to two MYT devices, then seven devices and a fresh 10,000-target pool.

**Tech Stack:** Python 3.12, SQLite, Appium/UiAutomator2, ADB, MYT SDK, FastAPI, React/TypeScript, Chrome Manifest V3, Node test runner, Playwright, pytest, Ruff.

---

## Authoritative Documents

- Product contract: `docs/superpowers/specs/2026-07-17-seven-account-acquisition-system-design.md`
- Mobile/core implementation: `docs/superpowers/plans/2026-07-17-acquisition-rounds-reliable-mobile.md`
- Browser implementation: `docs/superpowers/plans/2026-07-16-web-lead-conversion.md`
- Operator console: `docs/superpowers/plans/2026-07-17-operator-console.md`
- Historical seven-device plan: `docs/superpowers/plans/2026-07-16-seven-device-capacity.md`, superseded wherever it assumes imported metrics, native `uiautomator dump`, legacy tasks as final coverage, or unverified action clicks.

## Phase 1: Baseline And Existing Browser Checkpoint

- [ ] Run `git status --short --branch` and inspect every new user change.
- [ ] Run `uv run pytest tests/test_lead_conversion.py -q` and require all Task 3 policy tests to pass.
- [ ] Run `uv run pytest -q`, `uv tool run ruff check src tests`, `uv tool run ruff format --check src tests`, and `git diff --check`.
- [ ] Repeat the Task 3 specification review against the approved Web Task 3 examples, bilingual handoff rules, monotonic stages, invitation cooldown, contact priority, and prompt limits.
- [ ] Fix every specification gap through a failing test, focused pass, full pass, and separate commit.
- [ ] Repeat the Task 3 quality review for normalization, keyword boundaries, type consistency, prompt secrecy, and test readability.
- [ ] Fix every Critical or Important quality finding through a failing test and separate commit.
- [ ] Update `AGENTS.md` with the accepted Task 3 checkpoint.

## Phase 2: Workspace Hygiene

- [ ] Run `git status --short --branch` in both the primary checkout and active worktree.
- [ ] Run `git clean -ndX` and `git clean -nd` as read-only inventories; save no deletion command from their output.
- [ ] Remove only `.DS_Store`, Python cache, pytest cache, Ruff cache, stale ignored build directories, and superseded generated screenshots whose source and age are verified.
- [ ] Preserve `helloandworlder-tik/`, `TKAuto/`, CSV files, SQLite data, Chrome profiles, device diagnostics, and every directory with uncertain ownership.
- [ ] Add repository ignore patterns for `.DS_Store`, caches, local databases, local configs, screenshots, frontend build output, and device artifacts when absent.
- [ ] Run status again and commit only ignore-file changes; runtime artifact deletion remains outside Git history.

## Phase 3: Core Acquisition And Reliable Mobile

Execute Tasks 1-9 from
`docs/superpowers/plans/2026-07-17-acquisition-rounds-reliable-mobile.md` in order.

- [ ] Task 1: stable CSV identity and duplicate lineage.
- [ ] Task 2: acquisition repository and immutable target pools.
- [ ] Task 3: exposure rounds and deterministic device ordering.
- [ ] Task 4: first-visitor qualification snapshots.
- [ ] Task 5: independent outcome plans and fixed-hour quotas.
- [ ] Task 6: durable mobile assignment state machine.
- [ ] Task 7: Appium semantic action verification.
- [ ] Task 8: MYT discovery, allowlisted proxy relay, and fleet ownership.
- [ ] Task 9: CLI operations, capacity report, and coverage gate.

After every task, run its focused tests, the applicable full suite, Ruff check,
Ruff format check, `git diff --check`, specification review, quality review, and
one coherent commit.

## Phase 4: Browser Conversion Completion

Resume the browser plan after its accepted Task 3 checkpoint:

- [ ] Web Task 4: context-aware AI replies.
- [ ] Web Task 5: browser DM planning service.
- [ ] Web Task 6: browser DM, lease, and health HTTP endpoints.
- [ ] Web Task 7: pure JavaScript DM core.
- [ ] Web Task 8: Messages DOM observer and visible send executor.
- [ ] Web Task 9: lease-protected follow-back and extension options.
- [ ] Web Task 10: funnel persistence and sales, using acquisition round coverage rather than legacy task counts.
- [ ] Replace Web Task 11 with the approved operator-console plan instead of extending the legacy compact dashboard.
- [ ] Defer Web Task 12 live sending until production tone, offer facts, FAQ, private destination, and controlled sender accounts are configured.

Each inbound fingerprint retains one immutable draft and one action lease.
Account AI sending remains disabled or draft-only until the required production
conversation configuration is present.

## Phase 5: Operator Console

Execute `docs/superpowers/plans/2026-07-17-operator-console.md` in order after
mobile read models and Web Task 10 are available:

- [ ] Console Task 1: FastAPI application without route regressions.
- [ ] Console Task 2: acquisition read models and idempotent controls.
- [ ] Console Task 3: lead inbox, AI readiness, and human takeover APIs.
- [ ] Console Task 4: React operations workspace.
- [ ] Console Task 5: inbox, analytics, and manual workflow UI.
- [ ] Console Task 6: static integration and browser acceptance.

Before Console Task 4 implementation, invoke the frontend-design skill and
validate the visual direction against the approved quiet operational layout.
Before Console Task 6 completion, inspect Playwright screenshots at desktop and
mobile sizes and fix every overlap, clipping, blank state, broken asset, and
console error.

## Phase 6: Two-Device Live Gate

Execute Mobile Task 10 from the reliable-mobile plan:

- [ ] Discover both MYT slots and record ADB/Appium/proxy/TikTok/login health.
- [ ] Install TikTok on slot 2 and stop at a clearly recorded manual-login gate when session input is required.
- [ ] Calibrate trace, like, favorite, and repost against controlled targets on every ready account.
- [ ] Inject slow UI, Appium disconnect, app restart, and share-surface rerender; require reconciliation without duplicate toggles.
- [ ] Import the current 326-unique-user CSV and require exactly 652 assignments.
- [ ] Finish exact 2/2 completion with one shared snapshot per target, independent device plans, no quota overrun, no false completion, and an empty deferred queue.
- [ ] Publish measured mean/p90 separately from any daily projection.

## Phase 7: Web Live Gate

- [ ] Configure one dedicated Chrome profile per paired account and load the unpacked extension.
- [ ] Verify localhost heartbeat for Activity and Messages roles.
- [ ] From a controlled second account, create one new follow and verify one visible follow-back.
- [ ] Send three inbound messages and verify one immutable, language-matched reply per fingerprint.
- [ ] Verify buying signal, invitation policy, contact capture, and human takeover with synthetic production facts.
- [ ] Reload and rerender the thread and require zero duplicate sends.
- [ ] Keep the paired mobile worker advancing throughout browser actions.
- [ ] Repeat the seven checks for every configured account before enabling that account.

## Phase 8: Endurance And Production Capacity

- [ ] Obtain a representative pool large enough for uninterrupted four-hour execution.
- [ ] Run four hours and fix every material stall, false state, identity mismatch, quota breach, duplicate assignment, and unreconciled action.
- [ ] Repeat for eight hours and require stable memory, database, Appium, relay, Chrome, and worker health.
- [ ] Configure five additional paired devices/accounts and Chrome profiles, reaching seven ready mappings.
- [ ] Import a fresh operator-curated pool with at least 10,000 unique targets.
- [ ] Materialize exactly 70,000 assignments and verify different deterministic orders.
- [ ] Run for 20 effective hours and compute capacity from the slowest device's confirmed completions.
- [ ] Require per-device mean below 6.5 seconds, p90 below 8.64 seconds, zero false completions, zero quota overruns, empty deferred work, and exact 7/7 coverage.
- [ ] Publish the 10,000-unique-target daily capability only after this real gate passes.

## Final Delivery

- [ ] Run Python, Node, Android bridge, React, Playwright, lint, format, and diff verification from clean state.
- [ ] Inspect secrets and local data exclusions before any remote operation.
- [ ] Update every runbook and the `AGENTS.md` checkpoint with measured evidence and remaining operating prerequisites.
- [ ] Inspect `git remote -v`; configure GitHub only with the user's selected repository destination.
- [ ] Use the finishing-a-development-branch skill to choose merge, pull request, or retained worktree handling.
