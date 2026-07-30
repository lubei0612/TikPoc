# TikPoc Agent Guide

This file is the operating contract for coding agents working in this repository.
Read it before editing code. Detailed product requirements live in the linked
design and implementation documents; do not duplicate or silently reinterpret
them here.

## Current Control State

- **Active product direction (2026-07-28):** the high-volume profile-visit
  workflow is paused after repeated loss of visible visitor-record evidence.
  Do not resume it merely to satisfy the historical capacity queue below.
- **Approved live exception (2026-07-30):** browser/followers collectors may
  submit current live-room audience as `live_interrupt` profile-touch batches.
  Hybrid APK workers finish the current UI operation, process live targets
  before due comments, wait at the immutable participant barrier, then resume
  comment pacing and Home browsing. This exception does not resume the old CSV
  background visitor campaign.
- Continue the VMOS brand-comment session defined in
  `docs/superpowers/specs/2026-07-28-vmos-comment-session-design.md`: desktop
  discovery and comment evidence produce reviewed immutable plans; each account
  publishes at most 20 first-level comments per Asia/Shanghai day; the Android
  worker browses relevant feed content between due comments and pulls work over
  HTTPS without a persistent ADB dependency.
- Production assignment is globally exclusive by video: one TikTok video maps
  to exactly one brand account. Do not assign or publish comments from multiple
  IKUN BAGS accounts under the same video.
- A verification challenge stops comment gestures and triggers one bounded
  page reset using exactly two Android Back actions. Resume only after visible
  stable TikTok Home evidence; if the challenge remains, pause the affected
  device without a reset loop. VMOS Auto and TikPoc must not hold the
  accessibility-executor role at the same time.
- **Production mobile runtime decision (2026-07-26):** the user moved the
  project from MYT to VMOS. All new mobile account binding, acquisition,
  capacity, diagnostics, and catalog-publishing work must use VMOS devices.
  Do not discover, reconnect, configure, or schedule MYT slots unless the user
  explicitly authorizes a historical rollback test. Existing MYT documents and
  measurements remain historical evidence only; they are not current runtime
  instructions.
- The catalog-publishing matrix is historical. The current six-account runtime
  is the reviewed brand-comment workflow: 20 distinct first-level comment plans
  per account per Asia/Shanghai day, paced over the day with read-only Home-feed
  browsing between due comments. Keep account queues isolated and never
  resubmit an unresolved or visibility-ambiguous comment.
- The user resumed the project goal on 2026-07-17 and requested continuous
  execution after the revised design and plan are accepted.
- The user accepted the written design and authorized continuous execution on
  2026-07-17. Implementation may proceed through the approved roadmap.
- Live account actions, fleet workers, and runtime-service changes still require
  the applicable written live-test step, a healthy controlled account/device,
  and visible-state evidence. Do not treat implementation authorization as a
  blanket live-action command.
- Continue from the checkpoint in this file. Do not restart the project or
  repeat completed tasks.

## Mission

### Product Ownership Contract

- Treat TikPoc as an owned production product, not as a sequence of isolated
  prompts. Proactively inspect runtime evidence, choose the next highest-value
  task, implement it, verify it, commit it, deploy it, and measure the result.
- The active delivery objective is a stable measured day of at most `20`
  visibly confirmed, reviewed first-level comments per enabled VMOS account,
  with relevant read-only browsing between due comments. The historical
  `10,000`-profile-visit capacity target remains paused until a newer user
  instruction resumes that workflow.
- Do not wait for the user to select ordinary engineering steps. Continue from
  the latest durable checkpoint, keep a concise task queue, and stop for user
  input only when credentials, account login, paid infrastructure, or a visible
  human decision is genuinely required.
- Optimize the real bottleneck shown by durable stage timings and visible-device
  evidence. Do not spend time on cosmetic polish, speculative abstractions, or
  small-value refactors while correctness, stability, or capacity gates remain
  open.
- Own production quality: preserve secrets and user changes, keep rollback
  points, use GitHub for coherent commits, deploy only verified changes, and
  distinguish measured throughput from projections.

### Autonomous Capacity Work Queue

Execute these gates in order, repeating the diagnose/fix/verify loop at the
first failing gate:

1. **Single-device correctness:** fresh 20-target VMOS round; exact identity,
   visit, eligibility, immutable video choice, due interaction, one read-only
   uncertain reconciliation, and terminal accounting all pass with no duplicate
   interaction.
2. **Single-device performance:** fresh 100-target round and then an unchanged
   30-minute run; overall mean at or below `8.64 s`, zero stuck leases, and no
   route/action integrity regression. Track p90 and action/trace distributions
   diagnostically; slower interacted targets are acceptable when the complete
   target mix remains inside the average budget.
3. **Two-device isolation:** both APK workers pull over HTTPS without runtime
   ADB dependence; independent queues, sessions, order seeds, quotas, proxy
   exits, and visible actions remain isolated.
4. **Fleet scaling:** promote the same build to 6 devices, then 7 and 12 where
   available. Server scheduling and database contention must not reduce
   per-device throughput materially from the two-device baseline.
5. **Durability:** run an unchanged-build soak through Mac network changes and
   operator disconnects. APK checkpoints, server receipts, priority insertion,
   and parent-round resumption must survive restarts without duplicate visits or
   actions.
6. **Production acceptance:** complete at least one measured 24-hour capacity
   gate above `10,000` confirmed targets per enabled account, publish the
   measured funnel/capacity report, and retain rollback and audit evidence.

For each live gate, record target count, completed visits, confirmed action
counts, uncertain/failure counts, mean, p50, p90, targets/hour, runtime version,
device count, and whether the result is measured or projected. A short canary
may prove correctness but never proves the daily capacity objective.

TikPoc separates outbound VMOS mobile touch work from inbound web lead handling:

1. A configurable set of paired VMOS accounts processes the same imported
   target batch.
2. Every target must receive a confirmed visit from every enabled account
   (`N/N`).
3. Chrome profiles handle follow-back, new direct messages, AI-assisted
   multi-turn replies, private-channel invitations, and human takeover.
4. Mobile workers continue independently while the web flow handles leads.
5. The seven-account production benchmark is at least 10,000 unique targets per
   day, equal to 70,000 confirmed device-profile visits.
6. Measure the funnel through follows, inbound messages, qualified leads,
   private-channel invitations, captured contacts, human takeovers, and sales.

This throughput target is a capacity requirement, not permission to weaken
identity checks, action verification, idempotency, or coverage accounting.

## Sources Of Truth

Read these before implementing related work:

- `docs/superpowers/specs/2026-07-17-seven-account-acquisition-system-design.md`
  is the current system-level product and acceptance contract. It supersedes
  conflicting scheduling, interaction, coverage, and capacity details in older
  documents.
- `docs/superpowers/specs/2026-07-16-web-lead-conversion-design.md`
  defines the browser lead-conversion architecture and product behavior.
- `docs/superpowers/plans/2026-07-16-web-lead-conversion.md` contains Web Tasks
  1-12 and their test-first implementation steps.
- `docs/superpowers/plans/2026-07-16-seven-device-capacity.md` contains Fleet
  Tasks 1-6 and the capacity acceptance gates.
- `docs/superpowers/plans/2026-07-17-seven-account-acquisition-roadmap.md`
  defines the current cross-plan execution order and records which historical
  capacity assumptions it supersedes.
- `docs/superpowers/plans/2026-07-17-acquisition-rounds-reliable-mobile.md`
  contains the current target-pool, round, snapshot, interaction, MYT, mobile,
  and capacity implementation tasks.
- `docs/superpowers/specs/2026-07-19-paced-mobile-acquisition-design.md` and
  `docs/superpowers/plans/2026-07-19-paced-mobile-acquisition.md` supersede the
  older eligibility, equal outcome draw, and fixed-hour quota behavior with the
  current one-post eligibility and rolling pacing contract.
- `docs/superpowers/plans/2026-07-17-operator-console.md` contains the FastAPI,
  React, human-control, lead-inbox, and analytics tasks.
- `docs/superpowers/specs/2026-07-11-tiktok-mobile-automation-design.md` and
  `docs/superpowers/plans/2026-07-11-single-device-poc.md` describe the original
  mobile worker foundation.
- `docs/superpowers/specs/2026-07-16-web-engagement-bridge-design.md` and
  `docs/superpowers/plans/2026-07-16-web-engagement-bridge.md` describe the
  existing browser event bridge.

When this guide conflicts with an approved design or plan, follow the newer
user-approved document and update this guide in the same change.

## Architecture Boundaries

### Mobile touch plane

- VMOS is the production phone/device backend for imported CSV targets and
  catalog photo publishing. MYT is retired from current operations and remains
  only in historical evidence and rollback documentation.
- Runtime ADB endpoints, APK provisioning, Appium sessions, account identity,
  proxy state, and durable worker ownership must be resolved per VMOS instance.
  Never substitute a reachable MYT slot or another VMOS account when one VMOS
  instance is unavailable.
- The phone/device backend imports and visits CSV targets.
- Deeplink eligibility is `video_count >= 1`; following and follower metrics are
  retained for observation and reporting but do not gate interaction.
- Search experiments use immutable policy `search-posts-gte-1-composite-v1`: an exact profile with at least one visible post receives one planned like/favorite/repost outcome; a zero-post profile is trace-only. Search never clicks a similar username and never falls back to Deeplink.
- For an eligible profile, open one randomly selected video. Durable per-action
  token buckets and rolling quota headroom select a due like, favorite, or
  repost; when no action is due, retain the visit as trace-only.
- Trace-only means the confirmed profile/video visit is retained without an
  interaction action.
- Performance work must preserve this acquisition behavior. Never gain speed by
  weakening eligibility, turning an eligible due interaction into trace-only,
  skipping a selected action, reducing visible action verification, or changing
  durable multi-device coverage accounting.
- A profile-opening identity failure is recorded as skipped only when the
  visible page explicitly reports a missing/deleted or suspended account and
  the visible username matches the current target.
  Generic route failures, loading errors, and incomplete surfaces remain
  deferred because they may be device or network faults. Do not suppress the
  target globally from one device observation; every other configured device
  retains its assignment.
- Keep throughput fixes minimal and local. Do not add Inbox navigation, target
  classification, or new workflow branches unless required by approved business
  logic and separately verified against visible live state.
- Automatic mobile claims process untouched pending assignments before deferred
  repair work. Perform exactly one reconciliation for an uncertain action and
  never press its interaction control again. If that read remains uncertain, or
  an already-confirmed visit cannot reopen its unfinished target, terminate the
  automatic assignment while retaining the immutable plan, quota, failure and
  uncertain evidence. This operational terminal state is not an interaction
  confirmation and must remain visible as a capacity/promotion audit gap.
- Per-account rolling one-hour limits are: like `100`, favorite `14`, repost
  `25`.
- Rolling action usage and pacing are stored in the selected SQLite database.
  Never switch a live account to a fresh database until it has completed a full
  60-minute no-action cooldown measured from the latest non-trace reservation,
  action attempt, or later controlled-action evidence; every prior database has
  been checked; and no other worker can act for that account/device. Prefer a
  fresh pool and round in the persistent fleet database.
- A repost is complete only after the visible repost control inside the share
  surface has been activated and its resulting state has been verified.
- A visibly loaded share surface without a repost control records
  `repost_unavailable`, releases the action quota reservation, and completes the
  confirmed video visit as trace-only. It never records a repost success.
- Do not advance to the next target until the selected action has reached a
  terminal verified result or a recorded explicit failure/uncertain state.
- One device maps to one TikTok account. Never let two workers claim the same
  device/account pair.
- All enabled accounts process the same logical target batch. Coverage is based
  on durable confirmed visits, not task creation or attempted navigation.
- Strategy B groups five 200-target waves into each 1,000-target task. Each
  device uses an independent deterministic shuffle and applies no inter-device
  delay to the same target; coincidental nearby visits are acceptable, while a
  shared identical device order is not.

### Browser lead plane

- RoxyBrowser is retired from this project. Do not launch it or use its
  profiles for browser lead handling, live acceptance, or publishing.
- Each account uses a dedicated Chrome profile and account mapping.
- Observe TikTok Activity and Messages through visible DOM state. Perform
  follow-back and message sends through visible UI actions.
- The Chrome extension posts normalized, idempotent events to the localhost
  Python service. Python owns persistence, policy, AI planning, funnel state,
  and leases.
- One inbound fingerprint may produce at most one durable reply plan and one
  outbound send result.
- Every visible click/send requires an account-scoped action lease. A lease is
  exclusive even for the same owner until it expires or reaches its permitted
  terminal transition.
- `uncertain` results stay busy until reconciliation or lease expiry; never
  immediately retry them and risk a duplicate send.
- Browser work must not pause, preempt, or navigate a mobile touch worker.
- Do not read or export TikTok cookies, Chrome profile storage, session tokens,
  or browser credential databases.

## Conversation Policy

- Default autonomous budget: at most `12` AI replies per conversation.
- Reply in the sender's language, keep the response concise, and ask at most one
  qualifying question at a time.
- Use only configured offer and FAQ facts. Do not invent prices, inventory,
  delivery promises, discounts, payment instructions, or refund decisions.
- Invite to the configured private channel after a buying signal or after the
  second meaningful inbound turn.
- Apply a 24-hour invitation cooldown. Include the configured destination in
  the AI prompt only when the policy says an invitation is due.
- Contact capture and explicit private-channel acceptance take priority over a
  repeated invitation. Acknowledge the contact and mark the lead for closing.
- Payment, refund, complaint, cancellation, unsupported promises or discounts,
  and explicit requests for a human/agent/operator/representative/manager or
  `人工`/`客服`/`经理` require human takeover.
- Conversation stages are monotonic: `new`, `engaged`, `qualified`, `invited`,
  `contact_captured`, `human_required`, `closed`. Terminal or escalated states
  must not regress to ordinary AI handling.
- The first generated draft for an inbound fingerprint is immutable. Retries
  reuse the persisted plan rather than calling the model again.

## Capacity Contract

- Daily unique-target goal: `10,000`.
- Required seven-device visit count: `70,000`.
- Per-device daily visit count: `10,000`.
- Twenty-four-hour average budget: `8.64 s/target`.
- Promotion gate: each device's measured overall mean is at or below
  `8.64 s/target`, with identity, route, action, and `N/N` coverage checks
  passing. Interaction targets may exceed 8.64 seconds and trace targets may be
  faster; p50/p90 remain diagnostic rather than independent rejection gates.
- Report measured throughput separately from projected throughput. Do not claim
  the daily target from unit tests or a short synthetic run.

### Latest Capacity Checkpoint (2026-07-26)

- The current autonomous VMOS APK completed a fresh 20-target correctness gate
  with `20/20` confirmed visits and `20/20` terminal assignments.
- Confirmed interactions were one like and two reposts; one favorite reached
  `uncertain`, received exactly one read-only reconciliation, and remained
  visibly auditable without a second interaction press. Sixteen outcomes were
  trace-only and no duplicate interaction was recorded.
- The interval from first confirmed visit to last completion was approximately
  `194.6 s`, or about `370 targets/hour`. This is a correctness canary, not a
  capacity pass: it is below the `416.7 targets/hour` minimum needed for
  10,000/24h.
- The next active task is a fresh 100-target performance run followed by an
  unchanged-build 30-minute gate. Diagnose stage timings before changing code;
  prioritize route/open-video latency and avoid altering business decisions.

## Repository Map

- `src/tikpoc/`: Python domain logic, workers, SQLite persistence, HTTP service,
  messaging, dashboard, and CLI.
- `tests/`: Python unit and integration tests.
- `chrome-event-bridge/`: Manifest V3 extension and Node-tested pure helpers.
- `android-event-bridge/`: Android notification/event bridge and Java tests.
- `config/*.example.*`: committed configuration templates.
- `docs/`: approved designs, plans, and operational runbooks.
- `launchd/`: local service definitions.
- `TKAuto/`: reference material only; it is ignored and must not be copied into
  production code without understanding and tests.

## Development Workspace

- Primary repository checkout: `/Users/chenyuqi/Desktop/tik`
- Active feature worktree:
  `/Users/chenyuqi/.config/superpowers/worktrees/tik/web-lead-conversion`
- Active implementation branch: `feat/web-lead-conversion`
- Do feature work in the active worktree unless the user explicitly selects a
  different branch.
- GitHub `origin` is configured for the private repository. The active branch is
  pushed and draft PR `#1` tracks integration into `main`.
- Preserve unrelated user changes. Never use destructive resets or checkout
  commands to clean a dirty worktree.

## Required Work Method

### Delivery Priority

- Subagents used for this project must not run in fast mode. Use the standard
  inherited model configuration or a higher reasoning setting for every
  implementation, specification review, and code-quality review.
- Optimize for the shortest path to a working acquisition-to-private-channel
  business loop. Implement required business behavior before polish.
- Fix every Critical and Important correctness, duplication, account-isolation,
  state-integrity, or runtime-blocking issue before proceeding.
- Defer Minor findings, speculative multi-process support, broad refactors,
  style-only cleanup, and nonessential UI polish unless the fix is trivial and
  directly reduces risk to the current business workflow.
- Keep TDD and independent review gates focused: one clear red-green cycle, one
  specification review, and one code-quality review per task. Do not repeat
  review loops after only nonblocking Minor findings remain.
- Prefer a complete, testable multi-account workflow over optimizing isolated
  components that do not yet advance a lead toward private-channel conversion,
  human takeover, or a recorded sale.

For every plan task:

1. Read the relevant design section, plan task, current implementation, and
   nearby tests.
2. Use test-driven development. Add the smallest behavioral test and run it to
   observe the expected failure before editing production code.
3. Implement the smallest coherent change that makes the test pass.
4. Run focused tests, then the complete applicable suite and lint/format checks.
5. Commit one coherent task or review fix with a descriptive message.
6. Run an independent specification review against the approved plan.
7. Fix every specification gap with another red-green cycle and repeat the
   specification review.
8. Run an independent code-quality review. Address all Critical and Important
   findings and repeat the review.
9. Update checkpoint documentation before moving to the next task.

Do not combine multiple plan tasks merely to move faster. Do not mark a task
complete based only on the implementation author's report. Inspect the diff and
run fresh verification commands.

## Verification Commands

Use the active worktree as the working directory. Common commands are:

```bash
uv run pytest tests/PATH_TO_FOCUSED_TEST.py -q
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
git diff --check
git status --short --branch
```

`ruff` is currently available through `uv tool run`; do not treat a missing
`.venv/bin/ruff` as a source failure. Scale verification to the touched surface.
Run Python, Node, Java/build, and live checks when their respective components
change.

Live acceptance must verify the visible post-action state. A successful HTTP
response, DOM click call, ADB tap, or Appium command alone is not acceptance.
Never state that the complete flow works until real Chrome and device calibration
for the affected path has been performed and recorded.

## Data And Secrets

- Never commit or print model keys, passwords, cookies, tokens, private-channel
  destinations, personal contacts, or authenticated browser state.
- Store local values only in ignored files such as `.env.local`,
  `config/web-accounts.yaml`, `config/settings.yaml`, and `config/secrets/`.
- Keep SQLite files, logs, screenshots containing personal data, Chrome profile
  data, CSV exports, build artifacts, and Android debug signing material out of
  commits.
- Committed examples must use placeholders and synthetic fixtures.
- Redact secrets from test output, review notes, commits, and issue/PR text.

## Checkpoint (2026-07-19)

Completed on `feat/web-lead-conversion`:

- Web Task 1: browser-only account configuration.
- Web Task 2: durable reply plans and exclusive browser action leases.
- Web Task 3: reviewed lead-conversation policy with bilingual handoff,
  monotonic stages, invitation cooldown, contact priority, and prompt limits.
- Web Task 4: bounded account offer, FAQ, stage, conditional invitation, and
  per-account fallback context for AI replies.
- Web Task 5: idempotent browser DM planning, durable reply budgets, uncertain
  reconciliation gates, and migration-safe invitation evidence.
- Web Task 6: origin-validated browser DM, lease, and health HTTP endpoints with
  real Chrome extension transport coverage.
- Web Task 7: stable browser message identity, semantic control matching, and
  exact outbound reconciliation helpers.
- Web Task 8: serialized Messages observation, active-thread-bound visible send,
  outbound reconciliation, and browser health reporting.
- Web Task 9: account-scoped follow-back action leases and visible-result
  reconciliation. Further follow-back refinement remains secondary to the
  acquisition and private-channel workflow.
- Web Task 10: durable lead funnel, sales, and acquisition coverage projections
  with monotonic management stages.
- Operator Console Tasks 1-6: FastAPI operator APIs, idempotent acquisition
  controls, lead takeover/manual-plan/sale controls, responsive React
  Operations/Inbox/Analytics workspaces, embedded hashed assets, Uvicorn
  runtime, and desktop/mobile browser acceptance.
- Mobile Tasks 1-9: stable target identity, immutable pools, deterministic
  rounds, shared qualification snapshots, independent quota-controlled plans,
  durable workers, semantic Appium verification, MYT discovery, proxy relay,
  fenced fleet ownership, acquisition CLI operations, and strict capacity and
  coverage gates.
- Task 9 implementation commit: `b22bf69`
  (`feat: operate and gate acquisition capacity`).
- Task 9 quality-fix commit: `e58080c`
  (`fix: harden capacity and fleet shutdown gates`).
- Task 9 repeated specification and quality reviews passed with no Critical,
  Important, or Minor findings after the final lease-release fixes.
- Web Task 4 implementation commit: `a9c01d1`
  (`feat: add conversion context to AI replies`).
- Latest integrated console baseline before Task 6: `8517d8c`
  (`feat: add console direct route navigation`).
- Web Task 4 repeated specification and quality reviews passed with no Critical
  or Important findings after the fallback and provider-boundary fixes.
- Web Task 5 repeated specification and quality reviews passed with no Critical
  or Important findings. One future multi-process locking note is nonblocking
  for the single localhost service architecture.
- Web Task 6 repeated specification and quality reviews passed after browser
  Origin, JSON media-type, and real extension transport fixes.
- Task 6 focused static and CLI verification passed. Desktop `1440x1000` and
  mobile `390x844` Playwright acceptance passed for Operations, Inbox, and
  Analytics using a synthetic local database and registry.
- Last fresh full Python verification: `553 passed` with one nonblocking
  Starlette/httpx deprecation warning.
- Latest frontend verification: `26 passed`; production Vite build and wheel
  package-data inspection passed. Latest Chrome extension Node verification:
  `31 passed`.
- Ruff check passed. The five Task 6 Python files pass format-check; the
  full-repository format-check retains a pre-existing 10-file baseline outside
  this task. `git diff --check` and production dependency audit passed.
- Two nonblocking Task 4 review notes remain: explicitly migrate the dormant
  Business Messaging caller if that path is reactivated, and consolidate the
  duplicate prompt builders when Task 5 establishes the production call path.
- Read-only MYT discovery found running slot 1 at ADB/web ports `30000/30001`
  and slot 2 at `30100/30101`. No account action was performed.
- MYT slot 1 completed the current CSV functional gate with 326/326 completed
  assignments, confirmed visits, and snapshots. Final confirmed outcomes were
  21 favorite, 37 like, 37 repost, and 231 trace; no duplicate visits,
  nonterminal plans, uncertain quotas, or active leases remained.
- TikTok 44.8.42 compatibility now covers current profile/stat/post resource
  IDs, zero-idle Appium sessions, pixel-verified favorites, and stable user-ID
  routes for renamed handles. See `docs/mobile-fleet-runbook.md`.
- Rolling-hour pacing now spreads like/favorite/repost toward 100/14/25 per
  account, accepts profiles with at least one post, and keeps trace-only visits
  when no action is due.
- Blank stable routes now perform bounded route retries followed by one direct
  terminate-and-route restart, without using Inbox as a baseline. Route,
  identity, metrics, video, and action durations are persisted per assignment.
- The Chinese Operations console now shows rolling pacing and 20-hour capacity
  KPIs. Component tests pass 29/29 and Playwright passes 12/12 at 1440x1000,
  1920x1080, and 390x844 with inspected full-page screenshots.
- A fresh-database 326-target paced run completed 326/326 with zero uncertain
  plans or leases. Final-attempt mean/P90 were 4.658/6.980 seconds and the
  20-hour projection was 15,455 targets. This is measured 326-target evidence;
  the unchanged-build 500-unique-target gate remains open.
- Stable-ID routes that remain blank after the direct restart fall back to the
  public username URL and require exact visible username matching.
- Multi-account Browser Tasks 1-3 are complete: the localhost service exposes
  redacted arbitrary-account bindings, visible TikTok identity gates Activity
  and Messages actions, and each Chrome Profile selects one server mapping from
  the Chinese extension settings page.
- Rebinding requires explicit confirmation and clears only the old account's
  follower/DM baselines and processed records. Legacy DM records without an
  account owner remain intact.
- The extension popup shows Profile label, configured account, expected and
  observed TikTok usernames, and localized binding readiness at a stable 260px
  width. Desktop, 390px mobile, empty-list, mismatch, and page-level rebind
  Playwright checks passed without overflow.
- Multi-account Browser Task 3 specification and quality reviews passed after
  preventing binding-status writes from self-triggering Activity/DM scans.
- Multi-account Browser Task 4 is complete: all browser events, reply plans,
  reply results, action claims/results, and health requests pass through one
  account/device/visible-username boundary. Missing, ambiguous, signed-out,
  verification, and mismatch states block action-bearing requests with `409`.
- Browser health persists the server-evaluated binding state and observed
  username per account/page role, including blocked heartbeats. A client claim
  of `ready` never overrides a server-detected username mismatch.
- Task 4 specification and quality reviews passed with no remaining Critical or
  Important findings. Full Python verification passed `596` tests; Chrome Node
  verification remains `46` tests.
- Multi-account Browser Task 5 is complete: Operations and Inbox receive one
  Activity row and one Messages row for every configured account, with Profile,
  expected/observed username, server binding state, and heartbeat expiry.
- The Chinese console distinguishes `未绑定`, `身份不符`, `已退出`, `需验证`,
  `已就绪`, and `心跳过期`. AI reply and follow-back switches are independently
  scoped to the affected account and page role; no browser navigation controls
  were added.
- Task 5 specification and quality reviews passed. Full Python verification
  passed `597` tests, frontend component verification passed `31` tests, Chrome
  Node verification passed `46` tests, and Playwright passed `12` tests at
  1440x1000, 1920x1080, and 390x844 with inspected long-identity screenshots.
- Multi-account Browser Task 6 synthetic acceptance is complete. Equal
  follower, conversation, message, timestamp, fingerprint, and action inputs
  remain isolated across two accounts in extension storage and Python
  persistence; plans, leases, results, funnel events, and health rows are
  account-scoped.
- The Chinese web engagement runbook now documents arbitrary account rollout,
  one Profile per mapping, baselines, identity recovery, per-account switches,
  and the exact two-account live checklist.
- Task 6 synthetic regression passed `598` Python tests, `48` Chrome Node tests,
  `31` frontend tests, and `12` Playwright tests. Android build, Ruff check,
  touched-file format, JavaScript syntax, and `git diff --check` passed.
- Multi-account Browser Task 6 live acceptance resumed with two controlled
  Chrome Profiles. Both account identities were bound and Activity plus
  Messages health recovered to `4/4` ready after the localhost service restart.
- Mutual follow acceptance passed in both directions with visible `following`
  and friend states. Bidirectional manual DM delivery also passed and remained
  visible after reopening the conversations. No message body or destination was
  retained in this checkpoint.
- The live TikTok DOM required Messages-frame injection, Business Suite path
  recognition, current conversation/message selectors, geometry-based message
  direction, row-level conversation opening, and hashed preview signatures.
  The focused Chrome extension regression passes `67/67` tests.
- Account-scoped AI reply controls are enabled and automatic follow-back remains
  paused. No remote model environment variables are configured, so a successful
  automatic plan would use the configured per-account fallback acknowledgement.
- The final existing inbound acceptance message was visible, but it was recorded
  as the initial DM baseline while identity binding recovered. Aggregate durable
  state remained at zero reply plans and zero DM action leases, so no automatic
  reply or duplicate send was claimed. Automatic reply acceptance remains open.
- A local OpenAI-compatible provider was configured in ignored `.env.local` and
  passed a synthetic model request without exposing credentials. A real-account
  natural-language plan was generated with `plan_origin=ai`, and the matching
  exclusive `dm_send` lease was claimed once.
- Live debugging found two extension gaps: automatic bindings retained a disabled
  local DM switch instead of the server account policy, and stable `conv:<id>`
  conversation keys were reparsed as participant keys. Both now have focused
  red-green regression coverage and are fixed in the browser bridge.
- The controlled TikTok conversation is currently restricted by the platform:
  outgoing bubbles show a warning, disappear, and never arrive in the other
  Profile. The unexecuted AI plan was marked `superseded` and its claimed lease
  `uncertain`; no visible AI reply was claimed. Use a different message-capable
  controlled account for the remaining visible-send and reload-idempotency gate.
- The retained debug round failed capacity promotion: mean 170.718 seconds,
  P90 15.572 seconds, projected 421 targets per 20-hour day, with historical
  identity mismatch evidence. A fresh calibration-free round is still required.
- Last fresh full Python verification: `603 passed`; Chrome Node verification:
  `69 passed`; Android bridge build and Ruff check passed.
- The localhost console now has a fourth `/settings` workspace for one global
  OpenAI-compatible provider and per-account WhatsApp, Telegram, offer, FAQ,
  and reply-tone configuration. Provider keys are write-only; saved local
  settings are ignored, atomic, owner-only (`0600`), and hot-loaded for new
  plans.
- Qualified leads are asked to choose WhatsApp or Telegram before a destination
  is disclosed. After a preference, only the selected destination is available
  to the prompt, with a concise buying call to action. Existing cooldown,
  immutable-plan, reply-budget, contact-capture, and human-takeover rules remain.
- Browser auto-binding now returns the persisted per-account AI and follow-back
  switches instead of stale YAML defaults. Runtime private-channel settings also
  drive Inbox readiness immediately.
- A real provider connection test passed without retaining response content.
  Both controlled accounts report AI enabled, follow-back enabled, private
  channel configured, model configured, and Activity/Messages `4/4` ready.
- The latest controlled conversation persisted one message in each direction,
  but later consecutive bubbles disappeared after sender reload. Persisted
  inbound changes appeared only after receiver reload and were therefore
  treated as startup baselines. This round produced zero new reply plans,
  `dm_send` leases, and follow-back leases; visible autonomous send and fresh
  follow-back acceptance remain open and must not be claimed as passed.
- Latest full verification after the settings and synchronization fixes: Python
  `625 passed`, Chrome Node `69 passed`, frontend `35 passed`, Playwright `15/15`
  at desktop/wide/mobile, production build, Android build, Ruff check, and
  `git diff --check` passed.
- Supabase phase 1 is deployed to the Singapore `tikpoc-production` project.
  The committed migration creates central account, target-pool, target, run,
  device-health, lead, event, sale, and sync-checkpoint tables with RLS enabled;
  `anon` and `authenticated` table access is revoked. Secrets remain only in
  ignored owner-only files under `config/secrets/`.
- `supabase-pool-import` uses the same deterministic pool ID as SQLite and
  imports in idempotent 500-row batches. The real deduplicated export is stored
  centrally as one pool with `16,384` targets; count-only verification passed.
- Six MYT mobile account/device mappings and six health rows are in Supabase.
  All six slots are ADB-online with TikTok `44.8.42` and proxy
  `192.168.28.144:7897`; slot 1 is logged in and slots 2-6 are visibly at the
  login page.
- The Chinese console is running under launchd at `127.0.0.1:8766` from the
  active worktree with `/Users/Shared/TikPoc/tikpoc.db`. Its installed service
  uses `tikpoc serve`; the repository plist retains the `dashboard` alias so it
  remains installable from the not-yet-merged primary checkout.
- Pool imports remain hidden as `importing` until stale target rows are cleared,
  every batch succeeds, and the final count is published as `complete`. Central
  health enforces the configured account/device pair and lead stages cannot
  regress.
- Fresh verification for the Supabase phase passed `652` Python tests, Ruff
  check, touched-file format, plist validation, remote migration parity, live
  foreign-key/stage probes, and `git diff --check`.
- Full-repository Ruff format check has a pre-existing 8-file formatting
  baseline; do not reformat those unrelated files as part of a narrow task.
- Warm professional brand customer service and verified new-follower welcome
  behavior are implemented. Per-account settings now include brand name,
  default welcome language, and an explicit post-follow-back welcome switch.
- The first autonomous message introduces the configured brand AI service role
  once. Replies follow acknowledge/assist/advance/assure behavior, answer before
  qualifying, use one to three short sentences, and ask at most one question.
- A completed account-scoped follow-back plus its matching follower event creates
  one durable welcome plan per normalized follower username. Messages gives
  inbound work priority, uses an exclusive `welcome_send` lease, requires an
  exact target participant, suppresses welcomes for existing conversations, and
  reconciles the exact visible outbound bubble.
- Local IKUN brand, English welcome, business facts, and approved reply tone are
  stored only in the ignored owner-only settings file. Existing provider and
  private destinations were preserved and were not printed or committed.
- Fresh automated verification after the customer-service welcome change passed
  Python `642`, Chrome extension `76`, frontend `35`, production console build,
  Android build, Ruff check, touched-file format, JavaScript syntax, and
  `git diff --check`.
- A mixed-build 500-target MYT slot-1 diagnostic reached 500/500 with exact 1/1
  coverage after the unavailable-repost fix resumed two deferred assignments.
  Final-state mean/P90 were 4.790/7.756 seconds and the 20-hour projection was
  15,031 unique targets. This does not close the clean final-build gate.
- Two videos exposed a complete share surface without a Repost control. The
  requested reposts and diagnostic attempts remain durable, their reservations
  were released, and the verified visits completed as `repost_unavailable`
  trace-only outcomes. No repost success was inferred.
- The unchanged final build completed a fresh 100-target slot-1 preflight with
  exact 100/100 coverage and zero uncertain results. Mean/P90 were
  6.812/10.847 seconds and the 20-hour projection was 10,570 unique targets.
  It failed the capacity rule in effect at that time because p90 was an
  independent gate. Under the current user-approved overall-average contract,
  p90 is diagnostic and this timing distribution would pass the latency gate.
- Fresh integrated verification after the browser scan-health and mobile repost
  fixes passed Python `687`, Chrome extension `83`, browser-health Python `108`,
  frontend `35`, the production console build, Android build, Ruff check, and
  `git diff --check`.
- Real two-account welcome sends and the remaining automatic reply, reload,
  channel choice, invitation, contact-capture, and human-handoff gates still
  require fresh visible evidence. Do not infer them from automated regression.
- A user LaunchAgent now runs `tikpoc proxy-guard` every 30 seconds against the
  existing Clash Verge subscription. Six-device baseline and controlled slot-6
  stale-address recovery both passed; the final cycle reported six healthy
  devices and six TikTok HTTP `200` results. Subscription URLs and provider
  state remain outside TikPoc files and logs.
- The GXHY catalog source now exposes `tikpoc catalog scrape` for a shop URL or
  UID. It preserves public raw product text and structured details separately
  from the sanitized publishing model, emits AI-readable JSONL and per-product
  files, downloads images atomically with hashes and dimensions, reuses cached
  assets, isolates bad images, and blocks private-network image URLs.
- A bounded live TOP1-shop smoke export completed 2 products and 18/18 images;
  the same-directory rerun reused all assets. This is interface and small-batch
  evidence, not a claim that an entire shop has been exported. Focused catalog
  and CLI verification passed `45` tests; the full Python suite passed `802`
  with the existing Starlette/httpx deprecation warning. Ruff, touched-file
  format, CLI help, and `git diff --check` passed.
- The controlled slot-1 catalog publishing gate completed one TOP1-shop product
  as one five-image TikTok photo post. The Post control was activated exactly
  once; a first-use Profile modal caused the automatic result to freeze as
  `uncertain`, and read-only visible evidence on the exact expected account then
  reconciled that same job to `published`. Final durable status is
  `published=1`, `uncertain=0`; no duplicate or automatic retry occurred. This
  historical slot-1 acceptance predates the current six-VMOS fleet mapping and
  does not satisfy or replace any of the six fresh per-account canaries required
  for the current 90-post request.
- A representative 100-target VMOS canary reached 78 completions and 81
  confirmed visits before its ADB endpoint went offline. Measured throughput
  was about 183 confirmed visits per hour, below the 400-per-hour recovery gate.
  The disconnect caused one `AdbRouteError` and 21 immediate
  `WebDriverException` deferrals; those tail results are transport failures, not
  target outcomes.
- Video confirmation now performs one XPath fallback only when the fast
  semantic share-control lookup expires. Device transport exceptions are
  durably deferred and then propagated so the fleet supervisor restarts the
  worker instead of consuming the remaining queue through a dead session.
  Fresh verification passed Python `959`, Chrome Node `111`, touched-file Ruff
  and format checks, Android build, and `git diff --check`. Full-repository Ruff
  still has the existing 143-finding baseline.

Outstanding at the current checkpoint:

1. Discover the six active VMOS instances, verify one logged-in TikTok username
   per instance, bind those visible usernames to six unique internal account
   IDs, and refresh durable health. Do not reuse the retired MYT slot mappings.
2. Restore fresh Chrome Activity/Messages heartbeats. The four stored browser
   page records became stale after the service restart; no browser action was
   attempted while the Chrome control connection was unavailable.
3. Complete the remaining Multi-account Browser Task 6 verified post-follow
   welcome, automatic reply,
   reload-idempotency, channel-preference, single-destination invitation,
   contact-stage, human-handoff, and fresh follow-back live gates. Mutual follow,
   bidirectional manual DM delivery, and `4/4` browser health already passed; do
   not repeat them on a conversation whose later bubbles disappear after reload.
   Continue with a fresh controlled conversation that receives live DOM updates.
4. Run the fresh 100-target VMOS performance gate and then the unchanged-build
   30-minute gate. Promote latency when the complete target mix averages at or
   below 8.64 seconds; retain p50/p90 and per-action timings as diagnostics.
5. Execute the remaining Mobile Task 10 two-device live gate on two identity-
   verified VMOS instances.
6. Reconnect and verify all six VMOS control paths. Run one catalog-publishing
   canary per account, reconcile each visible post, then release at most the
   remaining 14 jobs for that account. Separately run a fresh isolated
   representative 100-target acquisition canary; do not reuse the interrupted
   database or infer a capacity pass until confirmed throughput reaches the
   documented gate.
7. Finish full regression, two-device calibration, four-/eight-hour endurance
   tests, the six-device VMOS gate, later seven-device benchmark where devices
   are available, runbooks, and branch/PR integration.

## Next Execution Procedure

1. Enter the active feature worktree and read this file plus both current plans.
2. Run `git status --short --branch` and `git log -5 --oneline`.
3. Preserve any new user changes and inspect them before proceeding.
4. Continue from the next approved live calibration or roadmap task without
   repeating completed operator-console work.
5. Repeat focused and full verification plus specification and quality review
   before committing the next task.

If runtime state differs from this checkpoint, report the concrete difference,
update the checkpoint, and continue from the latest verified state.

## Console Checkpoint (2026-07-18, Task 6)

- Console Tasks 1-5 are implemented. Task 5 evidence fields and analytics were
  reconciled in `3933d30`; focused frontend verification passed `26` tests.
- Console Task 6 serves the embedded Vite build from FastAPI at `/`,
  `/operations`, `/inbox`, and `/analytics`, with hashed immutable assets under
  `/console-assets/` and no-cache HTML.
- The production `tikpoc serve` command now runs the FastAPI application with
  Uvicorn. `tikpoc dashboard` and `dashboard.create_server` remain compatibility
  adapters for existing callers and tests.
- Direct console routes select the matching React workspace and browser
  back/forward navigation stays synchronized.
- Playwright acceptance passed `6/6` at `1440x1000` and `390x844`, covering all
  three workspaces, root overflow, control overlap, sticky coverage scrolling,
  long identities, and browser console errors. Evidence is ignored under
  `test-results/operator-console/`.
- Final regression passed `554` Python tests, `28` Vitest tests, `31` Chrome
  extension tests, and `8` Playwright tests. Ruff check, focused Python format,
  production dependency audit, Vite build, and `git diff --check` also passed.
- Independent Task 6 review findings were closed: console hosts are constrained
  to loopback, destructive stop commands require a responsive confirmation
  dialog, browser acceptance covers route history and runtime errors, and quota
  labels use fixed natural-hour semantics.
- Remaining production gates are controlled Chrome account calibration,
  two-device visible action verification, endurance runs, and the seven-device
  `10,000`-target/`70,000`-visit capacity proof. Do not describe those gates as
  passed from synthetic browser evidence.
