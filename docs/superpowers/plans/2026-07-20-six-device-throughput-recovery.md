# Six-Device Throughput Recovery Implementation Plan

**Goal:** Restore six-device acquisition throughput while retaining all durable
identity, action, quota, and resume gates.

**Architecture:** Replace blocking ADB activity waits with fire-and-verify
dispatch, enable compressed UiAutomator2 hierarchy, reuse one parsed profile
snapshot across readiness and qualification, and terminate bounded incomplete
metrics as an inaccessible trace.

## Task 1: Fast Route Dispatch

- Add a failing mobile-route test requiring `am start` without `-W`.
- Implement the minimal command change.
- Run mobile-route and fleet-runtime tests.

## Task 2: Compressed Session Hierarchy

- Add a failing driver test requiring both `waitForIdleTimeout=0` and
  `ignoreUnimportantViews=true`.
- Update session settings without changing ports or capabilities.
- Run runner and Appium adapter tests.

## Task 3: Reusable Visible Profile Snapshot

- Add failing adapter tests proving identity readiness parses one page source and
  the following observation reuses it.
- Introduce a private cached parsed profile observation scoped to the current
  route.
- Invalidate it before every route and after navigation away from the profile.
- Preserve exact identity and profile-surface validation.

## Task 4: Bounded Incomplete-Metrics Trace

- Add a failing worker/adapter test for visible identity with persistently
  incomplete metrics.
- Return an inaccessible observation only after the bounded read window.
- Publish the ineligible snapshot and complete the visit as trace.
- Keep mismatch and absent profile surfaces deferred.

## Task 5: Regression And Six-Device Canary

- Run focused tests, full Python tests, Ruff check/format, and `git diff --check`.
- Recheck six ADB devices, Appium, six proxy probes, and account profile state.
- Resume `round-ae3616f70853b1901e95` in the persistent database.
- Run six devices for 15 minutes, pause cleanly, and audit wall throughput,
  stages, coverage, retries, identity, actions, and quotas.
- Continue the round only if the approved gate passes; otherwise preserve the
  new checkpoint and iterate from measured evidence.

## Task 6: Consolidate Semantic Action Queries

- Add failing adapter tests that count semantic driver queries and require one
  pre-click query plus one fresh post-click verification query for like and
  favorite.
- Add a failing repost test requiring one pre-click state query while preserving
  the visible share, repost, and selected-state sequence.
- Implement one combined semantic state/control query per outcome and reuse the
  returned inactive control for execution.
- Run focused tests, full regression, Ruff, and diff checks.
- Run a fresh six-device canary from the same durable checkpoint and apply the
  existing 400-per-device-hour acceptance gate.
