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

## Task 7: Reuse Selected Semantic Post Elements

- Change the existing adapter test to require one post-container query across
  list, deterministic selection, and click.
- Add failing tests proving route, back, restart, and consumption invalidate the
  semantic element cache.
- Cache only Appium WebElements from the current visible profile and consume the
  cache during the immediately following video open.
- Preserve semantic click and visible Share-control verification; propagate
  stale or missing-control failures for durable defer.
- Run focused tests, full regression, Ruff, and diff checks.
- Run another clean five-minute six-device canary from the same checkpoint and
  apply the existing throughput and integrity gates.

## Task 8: Bound Appium Command Long Tails

- Add a failing runner test requiring a 20-second default Appium HTTP command
  timeout while preserving explicit override support.
- Change only the default command timeout; retain UiAutomator settings, unique
  ports, session lifetime, and worker recovery behavior.
- Run runner, fleet, adapter, full regression, Ruff, and diff checks.
- Run a clean ten-minute six-device canary from the same checkpoint.
- Compare throughput, identity mean/P90/max, deferred errors, uncertain actions,
  identity mismatches, quota integrity, worker health, and proxy health with the
  accepted build. Revert if stability regresses.

## Task 9: Correct Zero-Post Eligibility

- Add a failing adapter test for a public Profile whose parsed hierarchy reports
  zero posts while semantic post containers are visible.
- Use one semantic post query only for zero-post profiles whose following count
  is greater than followers, and cache the returned elements for video opening.
- Keep genuinely empty profiles ineligible and preserve private/inaccessible
  behavior.
- Run adapter, rule, worker, full regression, Ruff, and diff checks.
- Reclassify only pending work through fresh observations; do not rewrite prior
  confirmed visits or action evidence.
- Run a six-device canary and verify newly observed qualifying profiles receive
  planned interactions rather than `insufficient_posts` trace-only plans.

## Task 10: Bound Unreachable Profile Retries

- Add failing model/repository tests for a terminal `skipped` phase, durable
  `profile_unreachable` evidence, released leases, and round operational
  completion when `completed + skipped == total`.
- Add failing worker tests proving profile-opening `ValueError` defers attempts
  one and two, skips attempt three, and never records a confirmed visit.
- Add negative tests proving `ProfileIdentityMismatch`, errors after identity
  confirmation, video failures, and uncertain action work remain deferred.
- Implement one repository-owned skip transition and a worker policy that
  checks the current durable phase, visit evidence, exact exception type, and
  attempt count before using it.
- Keep coverage and capacity strict: skipped assignments stay incomplete for
  `N/N`, while fleet operational exhaustion accepts completed plus skipped.
- Run mobile worker, acquisition repository/service, fleet runtime, capacity,
  full Python, Ruff, format, and diff verification.
- Resume only pending/deferred work in the existing production round. Do not
  rewrite prior completed assignments or silently convert the six currently
  deferred assignments.
