# Windowed Coverage And Throughput Design

## Objective

Raise measured mobile capacity to at least 3,000 confirmed profile visits per
device per 24 hours before six-device production resumes, then continue toward
4,000 per device per day. Preserve exact visible identity confirmation, durable
visit evidence, one immutable interaction plan, quotas, and duplicate safety.

## Acceptance Targets

- Initial promotion gate: at least 125 confirmed profile visits/hour/device,
  projected from a representative live run long enough to include interactions
  and failures.
- Next target: at least 167 confirmed profile visits/hour/device.
- Six-device coverage uses the same target set and must form complete 6/6 groups
  promptly rather than only near the end of a 1,000-target batch.
- Throughput is measured from confirmed visits. Attempts, task claims, and unit
  tests do not count.

## Windowed B Strategy

- Keep the existing 1,000-target business batch and durable batch checkpoint.
- Divide the active batch into consecutive 100-target coverage windows.
- All active devices work on the same current window.
- Each device uses a deterministic, independently shuffled order inside that
  window.
- A device that finishes early does not advance alone. It remains idle while the
  scheduler resolves the window's terminal records.
- Advance to the next window only after every target-device assignment in the
  current window is terminal: completed, skipped, or uncertain-terminal.
- Coverage reporting distinguishes confirmed 6/6 visits from terminal window
  completion when one or more devices skipped a target.
- Priority work saves the ordinary window and per-device checkpoint, completes
  the priority window, and then resumes the saved window.

## Fail-Fast Target Handling

Each claimed target gets one forward execution attempt in the current run.

- Profile route, exact identity, profile parsing, post opening, or interaction
  verification error: record the bounded error and make that device-target
  assignment terminal immediately.
- If exact profile identity was confirmed before a later failure, preserve the
  confirmed visit timestamp.
- If identity was not confirmed, record a skipped visit without manufacturing
  coverage.
- An action returning uncertain is recorded once and becomes terminal. Do not
  reopen the profile or video for read-only reconciliation during the fast path.
- Never apply an interaction twice. Manual or later offline analysis may inspect
  uncertain results without changing the completed window.
- Explicit unavailable, missing, suspended, or identity-mismatched profiles are
  terminal on first observation.

## Fast Interaction Path

- A profile is interaction-eligible when at least one visible post exists.
- The server creates one immutable outcome and video handle.
- The APK opens the selected post once and applies at most one action.
- It uses only bounded evidence already available on the current video surface:
  selected state, visible count transition, or visible repost confirmation.
- The total post-action observation budget is capped at 1.5 seconds. Absence of
  confirmation records `uncertain` and advances.
- Trace-only confirms the visit without an action.
- Existing rolling quotas remain authoritative.

## Timing Budget

The initial 125/hour gate permits an average of 28.8 seconds per target. The
implementation targets:

- route plus exact identity: at most 10 seconds;
- profile observation and plan retrieval: at most 5 seconds;
- post open: at most 7 seconds;
- interaction and evidence: at most 1.5 seconds;
- deterministic pacing: 0.2 to 1.2 seconds;
- remaining budget for network and persistence.

Timeouts are phase-specific. A phase timeout terminates the current assignment;
it does not enter a retry loop.

## State And Idempotency

- Add an explicit terminal assignment outcome for bounded fast-path failure while
  retaining its error code and visit-confirmed timestamp.
- A single idempotency identity covers each assignment phase result. Repeated
  uploads return duplicate without reopening work.
- No terminal assignment can be claimed again automatically.
- A window checkpoint contains batch id, window index, target range, per-device
  terminal counts, and confirmed coverage counts.

## Observability

Record per device and per phase:

- confirmed visits/hour over 15-minute, 60-minute, and full-run windows;
- mean, p50, and p90 assignment duration;
- route, identity, profile, post, action, and pacing elapsed time;
- terminal error counts;
- interaction confirmed and uncertain counts;
- 1/6 through 6/6 coverage distribution;
- duplicate visits and duplicate action attempts.

A stuck-task alarm fires when one assignment remains nonterminal beyond twice
its phase budget or its attempt count exceeds one.

## Rollout And Verification

1. Unit and integration tests prove first-error terminal behavior, no automatic
   reconciliation, no repeated claims, window barriers, deterministic shuffled
   order, and checkpoint resumption.
2. Run full Python and Android regressions and lint/format checks.
3. Deploy to one VMOS device and run 20 targets for correctness.
4. Run 100 representative targets. Require exact identity evidence, no duplicate
   interactions, and at least 125 confirmed visits/hour.
5. Run an uninterrupted 30-minute gate with enough queued work. Require at least
   125 confirmed visits/hour and no stuck assignments.
6. Expand to six devices on one 100-target shared window. Require all healthy
   devices to finish the window, correct coverage accounting, and no device
   below 125 confirmed visits/hour.
7. Resume the saved production batch only after these gates pass. Continue
   optimizing toward 167/hour without weakening the business rules above.
