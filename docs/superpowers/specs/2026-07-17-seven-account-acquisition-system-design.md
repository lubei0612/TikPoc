# TikPoc Seven-Account Acquisition System Design

**Status:** Proposed for implementation

**Date:** 2026-07-17

**Supersedes:** Conflicting mobile scheduling, interaction, coverage, and
capacity details in earlier TikPoc specifications. The browser conversation
contract in `2026-07-16-web-lead-conversion-design.md` remains authoritative
where this document does not refine it.

## 1. Goal

TikPoc is a single-operator acquisition system with three independent planes:

1. Seven paired mobile accounts repeatedly expose the same curated prospects to
   prepared TikTok profiles.
2. Seven paired Chrome profiles follow back and handle inbound direct messages
   without interrupting mobile work.
3. A local management console controls batches, exposure rounds, devices,
   quotas, retries, browser health, conversations, and funnel results.

The daily production requirement is 10,000 deduplicated prospects with complete
seven-account coverage, equal to 70,000 confirmed device outcomes per day. The
commercial funnel is:

`curated target -> 7/7 exposure -> profile interest -> follow/DM -> concise AI response -> private-channel invitation -> human close -> sale`

The system optimizes confirmed private contacts and sales, while retaining exact
visit, interaction, follow, conversation, and invitation measurements.

## 2. Confirmed Product Decisions

- The operator supplies curated comment CSV files in the same 15-column format
  as the current test file.
- Targets are deduplicated before assignment. Every deduplicated target is
  assigned once to each of seven devices in every exposure round.
- Devices process the same logical target set in different deterministic orders.
- A target's first device visit in an exposure round reads and publishes the
  profile metrics. The other six devices may reuse that round-scoped snapshot
  only after confirming the profile identity on their own screen.
- The shared snapshot determines eligibility only. Every device independently
  selects its own video and interaction outcome.
- Eligible means `following > followers` and `post_count > 3`.
- An eligible device outcome is independently selected from like, favorite,
  repost, and trace-only with equal 25 percent weights before quota handling.
- Per-account fixed natural-hour limits are 100 likes, 14 favorites, and 25
  reposts. Trace-only has no hourly limit.
- If the selected interaction has no remaining quota, that assignment becomes
  trace-only. Its probability is not redistributed to another interaction.
- A generic share-panel open is not a repost. Repost requires the visible
  Repost control to be activated and its resulting UI state verified.
- No assignment advances merely because a click command returned. It advances
  only after a visible terminal result or enters a durable deferred-retry state.
- A batch may run for multiple exposure rounds across multiple days. Each round
  receives independent coverage, metric snapshots, order keys, video choices,
  and device action plans.
- Web AI answers basic questions concisely. Detailed or repeated product
  questions should be acknowledged and moved politely toward the configured
  private channel. The operator will supply production tone, offer facts, FAQ,
  and private-channel details before live autonomous sending is enabled.

## 3. Scope And Boundaries

### Included

- Structured import of the current comment CSV format.
- Per-pool target deduplication with source-row lineage.
- Campaigns and repeatable exposure rounds.
- Seven-device deterministic shuffle, complete assignment, and restart-safe
  continuation.
- MYT device discovery and health, ADB fast navigation, and Appium/UiAutomator2
  semantic inspection and verification.
- Shared round-scoped profile qualification snapshots.
- Independent per-device video and action plans.
- Fixed-hour per-account quotas with crash-safe reservations.
- Verified like, favorite, repost, and trace-only outcomes.
- Deferred recovery for slow, disconnected, or ambiguous cloud-device states.
- Chrome DOM follow-back and AI-assisted direct-message handling.
- Manual conversation takeover and account-level enable switches.
- Operational dashboard, capacity reporting, screenshots, and funnel metrics.

### Excluded

- Account creation, automated login, CAPTCHA solving, and identity verification.
- Copying authenticated browser state between profiles.
- Treating HTTP, ADB, Appium, or DOM command success as visible action success.
- Counting attempted navigation, queued work, or unverified clicks as completed
  coverage.
- Enabling live AI replies before account-specific facts, tone, and destination
  are configured and reviewed.

## 4. Input And Identity

The import adapter accepts the current UTF-8 CSV header:

`sequence, source_type, video_id, video_url, commenter_user_id, commenter_sec_uid, commenter_handle, commenter_nickname, commenter_profile_url, comment_cid, comment_text, comment_like_count, reply_depth, is_second_level_reply, collected_at`

The actual source may retain its current Chinese header names. The adapter maps
them to normalized fields rather than depending on column positions.

Target identity precedence is:

1. normalized `commenter_sec_uid`;
2. normalized `commenter_user_id`;
3. normalized handle only when neither stable identifier exists.

Duplicate source rows remain available as lineage, but create one logical target
per target pool. The current fixture has 372 source rows and 326 unique users; it
is suitable for functional and two-device coverage testing, not a sustained
one-hour 500-target-per-device benchmark.

## 5. Campaigns, Rounds, And Queue Ordering

A target pool is immutable after import. A campaign references one pool and owns
one or more exposure rounds. Creating a round materializes exactly one assignment
for every `(round_id, target_id, device_id)` combination.

Each device has a persisted seed. Its order key is a deterministic hash of the
round, device seed, and target identity. This provides different orders without
using restart-variant database randomness. Restarting resumes the same remaining
order without omission or duplication.

The scheduler prevents two devices from actively entering the same target at
the same time and enforces a default 15-minute spacing between confirmed visits
to that target by different devices. The interval is configurable per campaign,
but changing it is recorded with the round. A blocked assignment yields
temporarily so the device can process another target. This preserves throughput
while avoiding synchronized seven-account visits.

A later repeat exposure creates a new round rather than resetting old tasks.
The default schedule permits one round per target pool per calendar day and
requires at least 20 hours between a device's confirmed visits to the same
target in consecutive rounds. Historical round results remain immutable and
comparable. A round is complete only when every target has a confirmed terminal
outcome from every required device.

## 6. Shared Qualification Snapshot

Because the CSV does not contain following, follower, or post counts, TikPoc
obtains them from visible profile UI.

The first device reaching a target in a round acquires a short qualification
lease, confirms the visible profile identity, reads all three metrics, evaluates
the rule, and persists one `profile_snapshot`. Other devices:

1. still open the target profile;
2. independently confirm the visible identity;
3. reuse the completed snapshot for eligibility;
4. independently continue to their own outcome plan.

Devices arriving while a snapshot is pending yield the assignment and revisit
it later instead of waiting idle. A failed or ambiguous metric read does not
publish an eligibility result. Another device may take the lease after expiry.

Snapshots are scoped to one exposure round. A new round reads one fresh snapshot
per target so multi-day campaigns do not rely indefinitely on old counts. A
private, missing, suspended, or inaccessible profile records a typed observation
and completes as an identity-confirmed profile trace when the visible state is
unambiguous.

## 7. Independent Outcome Planning

After an eligible snapshot exists, every device creates a durable plan unique on
`(round_id, target_id, device_id)`:

- independently select one visible video from that profile;
- independently draw one of like, favorite, repost, or trace-only at 25 percent;
- persist the video candidate identity, draw, selected outcome, quota decision,
  and random seed before performing UI actions.

Independent draws may coincidentally choose the same outcome on multiple
devices. They never copy another device's selected outcome. A retry reuses the
persisted plan instead of drawing again.

An ineligible target completes after the device's profile identity-confirmed
trace. An eligible trace-only target opens and confirms the planned video but
does not press an interaction control.

## 8. Hourly Quotas

Quota windows are fixed natural hours with boundaries at minute `00`. They are
scoped to a device/account and action type. The defaults are:

| Action | Limit per account-hour |
| --- | ---: |
| Like | 100 |
| Favorite | 14 |
| Repost | 25 |
| Trace-only | Unlimited |

Quota reservation and action-plan persistence occur in one transaction. A
restart does not release a reservation. An uncertain action continues consuming
its reservation until reconciled or the window ends, preventing accidental
overrun. When the selected action is full, the plan records `quota_trace` and
performs trace-only; it does not redraw.

The dashboard displays current window start, reserved, confirmed, uncertain,
remaining, and next reset time for every account and action.

## 9. Mobile Execution And Verification

The mobile path combines three layers:

- MYT discovery and instance health for slots, Android state, and port mapping;
- ADB for low-latency app wake-up and profile deep-link navigation;
- Appium/UiAutomator2 with `waitForIdleTimeout=0` for semantic page inspection,
  element interaction, and visible post-action verification.

MYT traffic uses a dedicated relay bound to the Mac LAN address. The relay
forwards to the existing host proxy and allowlists only the configured MYT host;
the underlying desktop proxy is not opened to the entire LAN. Relay health and
the effective Android proxy endpoint are visible per device.

Each assignment follows a persisted state machine:

`claimed -> profile_opening -> identity_confirmed -> snapshot_ready -> video_opening -> video_confirmed -> quota_reserved -> action_executing -> action_reconciling -> outcome_confirmed -> completed`

Important rules:

- Every phase has an observable entry condition, success condition, deadline,
  error category, and recovery action.
- Adaptive condition waits replace fixed sleep-and-advance behavior.
- A like is confirmed only by the visible selected/Liked state.
- A favorite is confirmed only by the visible selected/saved state or an
  equivalent calibrated success signal.
- A repost opens the share surface, activates the visible Repost control, and is
  confirmed by Reposted, Remove repost, or another calibrated completed state.
- Trace-only confirms the intended profile or video identity and configured
  dwell completion without pressing an interaction control.
- Before retrying a click after a timeout, reconciliation reads the current
  state. It never blindly toggles a control that may already be active.
- The next target is unavailable to that worker until the current assignment is
  confirmed or durably deferred.

The default slow-device recovery budget is three local recovery attempts within
90 seconds. Exhaustion moves the same immutable plan to a high-priority deferred
queue and releases the device to useful work. Deferred work remains incomplete
and must later be confirmed; it never becomes successful coverage automatically.
The dashboard exposes its phase, attempts, diagnostic UI data, and screenshot.

## 10. Coverage Semantics

TikPoc tracks two related facts separately:

- `profile_visit_confirmed`: the device visibly reached and identity-matched the
  target profile.
- `assignment_completed`: the required ineligible trace, eligible trace-only, or
  verified interaction outcome reached its terminal confirmed state.

Operational progress may show both, but a round's required `7/7` result uses
`assignment_completed`. An eligible action that is pending, deferred, failed,
or uncertain does not count. An ineligible target counts only after its profile
trace is identity-confirmed.

The round coverage matrix shows every target against all seven accounts, the
planned outcome, current phase, confirmed result, duration, and retry state.

## 11. Browser Lead Plane

Each mobile account maps to one dedicated Chrome profile. Its Activity and
Messages tabs remain independent of the mobile worker.

The extension reacts to visible DOM changes, claims account-scoped actions from
the localhost service, follows back through the visible Follow control, and
sends one persisted reply plan per inbound fingerprint. Rerenders, reloads, and
duplicate tabs cannot produce duplicate replies.

Conversation behavior is intentionally narrow:

- answer in the sender's language;
- keep replies concise and ask at most one simple qualifying question;
- use only configured offer and FAQ facts;
- acknowledge detailed product questions and politely guide the sender to the
  configured private channel rather than inventing specifics;
- invite after a buying signal or the configured meaningful-turn threshold;
- capture contact details and mark the lead for human closing;
- hand over payments, refunds, complaints, unsupported promises, and explicit
  human requests;
- use a default hard maximum of 12 autonomous replies, with the ordinary path
  expected to complete basic handling within one to three replies.

Until production account context and destination are provided, live autonomous
sending remains disabled or draft-only. Synthetic fixtures and controlled
accounts cover implementation and calibration first.

## 12. Management Console

The management surface uses a local FastAPI service and a React/TypeScript
frontend. It remains bound to `127.0.0.1`; existing durable APIs are migrated
behind compatible routes rather than splitting product state across services.
It provides:

- CSV validation, import statistics, deduplication results, and target-pool
  history;
- campaign and exposure-round creation, scheduling, pause, resume, stop, retry,
  and archive controls;
- fleet-wide and per-device controls with MYT, ADB, TikTok, login, proxy, and
  Appium health;
- current target, phase, planned outcome, elapsed time, retries, errors, and
  screenshots;
- current hourly quotas and reset time;
- per-device hourly confirmed rate, mean, p90, slowest-device projection, and
  measured versus projected daily capacity;
- target-by-device visit and completion coverage matrices;
- Chrome profile heartbeat, page-role, signed-in, selector, and action health;
- follow, DM, qualification, invitation, contact, takeover, sale, and revenue
  funnel views;
- AI draft review, manual takeover, send enable switches, FAQ/tone/destination
  readiness, and account-profile readiness checks.

Controls use leases and idempotency keys. Repeated operator clicks cannot start
two workers on the same device or duplicate a browser action.

## 13. Persistence Model

SQLite remains the single product-state source. Additive schema changes provide:

- `target_pools` and `target_source_rows`;
- `campaigns` and `exposure_rounds`;
- `round_assignments`, unique by round, target, and device;
- `device_order_seeds` and persisted order keys;
- `profile_snapshot_leases` and `profile_snapshots`;
- `device_action_plans` with immutable random and video decisions;
- `action_attempts` with pre-state, post-state, result, timing, and diagnostics;
- per-device fixed-hour quota windows;
- deferred retry scheduling and assignment phase history;
- browser reply plans, browser action leases, conversation state, and funnel
  events already defined by the web conversion design.

All state transitions use explicit allowed transitions and transactions.
Screenshots, UI trees, CSV files, databases, Chrome state, and secrets remain
ignored local runtime artifacts.

## 14. Capacity Contract

The capacity equation is fixed:

- 10,000 unique targets per day;
- seven confirmed assignments per target;
- 70,000 confirmed assignments per day;
- 10,000 confirmed assignments per device per day.

The design uses a 20-effective-hour operating target and four hours of recovery,
maintenance, login, proxy, and retry headroom. Each device therefore needs 500
confirmed assignments per effective hour, or 7.20 seconds per assignment.

Promotion requires, on every device:

- sustained mean below 6.5 seconds per confirmed assignment;
- p90 below 8.64 seconds;
- zero identity substitutions;
- zero quota overruns;
- zero false completed outcomes;
- zero omitted or duplicated assignments;
- complete deferred-queue reconciliation;
- exact 7/7 round coverage.

The dashboard projects daily capacity from the slowest healthy device's stable
confirmed completion rate, not the fleet average and not raw navigation attempts.
Measured and projected numbers remain separate.

## 15. Verification And Acceptance

### Automated

- CSV header mapping, BOM handling, identity precedence, deduplication, and
  lineage.
- Round materialization, deterministic per-device ordering, restart resume,
  active-target spacing, and exact coverage.
- Snapshot lease exclusion, expiry takeover, incomplete metric rejection, and
  new-round refresh.
- Independent device draws, deterministic retry reuse, equal configured weights,
  quota fallback, fixed-hour rollover, and crash recovery.
- Every state transition, invalid transition, timeout, deferral, and
  reconciliation branch.
- Semantic like, favorite, repost, and trace verification adapters against
  recorded UI fixtures.
- Browser event fingerprinting, reply-plan immutability, action leases, send
  reconciliation, invitation policy, and human takeover.
- Dashboard control idempotency, API validation, capacity calculations, and
  funnel aggregation.

### Live Two-Device Gate

Use the current 326-unique-user CSV on MYT slots 1 and 2 after both slots have
TikTok installed, logged in, and proxy/ADB/Appium health passing.

Require:

- exactly 652 assignments and complete 2/2 terminal coverage;
- different deterministic device orders;
- one shared snapshot per target per round;
- independent device action plans;
- controlled successful calibration of all four outcomes;
- no target advancement before visible terminal verification;
- successful recovery from injected UI delay, Appium disconnect, app restart,
  and share-surface rerender;
- no duplicate toggles, quota overruns, or false success records;
- an empty deferred queue at final acceptance.

This fixture proves behavior and two-device coverage. Its 326 targets do not
prove one-hour or daily production capacity.

### Endurance And Seven-Device Gate

1. Run a representative larger pool for four continuous hours.
2. Run an eight-hour endurance test after fixing every material stall and false
   state found in the four-hour run.
3. Configure seven paired devices and Chrome profiles.
4. Run a fresh 10,000-target production-like round for 20 effective hours.
5. Require all capacity, action, quota, recovery, browser, and 7/7 gates to pass.

TikPoc reports the 10,000-target daily capability only after the real seven-device
gate passes. Short runs and unit tests may provide projections but not the final
claim.

## 16. Delivery Sequence

1. Re-establish the clean test baseline and complete the pending Web Task 3
   specification and quality reviews.
2. Audit the workspace and remove only proven generated or duplicate artifacts;
   preserve reference projects and user data.
3. Implement target pools, exposure rounds, seven-device deterministic ordering,
   and coverage semantics.
4. Implement shared qualification snapshots and independent action plans.
5. Harden MYT/ADB/Appium execution, visible action verification, and deferred
   recovery on the two available slots.
6. Build the operator management console over the durable fleet state.
7. Finish Chrome follow-back, AI DM handling, manual takeover, and funnel views.
8. Complete two-device functional acceptance and four-/eight-hour endurance
   tests.
9. Add and calibrate the remaining five devices and Chrome profiles.
10. Run the full seven-device, 10,000-target capacity gate and publish the
    measured operating runbook.

Every implementation task follows test-driven development, focused and full
regression, specification review, quality review, and a coherent commit before
the next task begins.
