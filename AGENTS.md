# TikPoc Agent Guide

This file is the operating contract for coding agents working in this repository.
Read it before editing code. Detailed product requirements live in the linked
design and implementation documents; do not duplicate or silently reinterpret
them here.

## Current Control State

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

TikPoc separates outbound mobile touch work from inbound web lead handling:

1. Seven paired mobile accounts process the same imported target batch.
2. Every target must receive a confirmed visit from all seven accounts (`7/7`).
3. Chrome profiles handle follow-back, new direct messages, AI-assisted
   multi-turn replies, private-channel invitations, and human takeover.
4. Mobile workers continue independently while the web flow handles leads.
5. The production target is at least 10,000 unique targets per day, equal to
   70,000 confirmed device-profile visits for seven-account coverage.
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

- The phone/device backend imports and visits CSV targets.
- Eligibility is `following > followers` and `video_count > 3`.
- For an eligible profile, open one randomly selected video and choose exactly
  one outcome: like, favorite, repost, or trace-only. The four outcomes are
  evenly weighted before quota constraints are applied.
- Trace-only means the confirmed profile/video visit is retained without an
  interaction action.
- Per-account fixed natural-hour limits are: like `100`, favorite `14`, repost
  `25`.
- A repost is complete only after the visible repost control inside the share
  surface has been activated and its resulting state has been verified.
- Do not advance to the next target until the selected action has reached a
  terminal verified result or a recorded explicit failure/uncertain state.
- One device maps to one TikTok account. Never let two workers claim the same
  device/account pair.
- All seven accounts process the same logical target batch. Coverage is based on
  durable confirmed visits, not task creation or attempted navigation.

### Browser lead plane

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
- Twenty-hour average budget: `7.20 s/target`.
- Promotion gate: per-device mean below `6.5 s` and p90 below `8.64 s`, with
  identity, route, action, and `7/7` coverage checks passing.
- Report measured throughput separately from projected throughput. Do not claim
  the daily target from unit tests or a short synthetic run.

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
- A GitHub remote has not yet been configured. Inspect `git remote -v` before
  offering to push or open a pull request.
- Preserve unrelated user changes. Never use destructive resets or checkout
  commands to clean a dirty worktree.

## Required Work Method

### Delivery Priority

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
- Blank stable routes now perform bounded route retries, an Inbox baseline
  reset, and one TikTok restart before a terminal failure. Route, identity,
  metrics, video, and action durations are persisted per assignment.
- The Chinese Operations console now shows rolling pacing and 20-hour capacity
  KPIs. Component tests pass 29/29 and Playwright passes 12/12 at 1440x1000,
  1920x1080, and 390x844 with inspected full-page screenshots.
- A fresh-database 326-target paced run completed 326/326 with zero uncertain
  plans or leases. Final-attempt mean/P90 were 4.658/6.980 seconds and the
  20-hour projection was 15,455 targets. This is measured 326-target evidence;
  the unchanged-build 500-unique-target gate remains open.
- Stable-ID routes that remain blank after baseline and one restart now fall
  back to the public username URL and require exact visible username matching.
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
- Real two-account welcome sends and the remaining automatic reply, reload,
  channel choice, invitation, contact-capture, and human-handoff gates still
  require fresh visible evidence. Do not infer them from automated regression.

Outstanding at the current checkpoint:

1. Let the user complete TikTok login on MYT slots 2-6, then bind visible
   usernames to the six internal account IDs and refresh Supabase health.
2. Restore fresh Chrome Activity/Messages heartbeats. The four stored browser
   page records became stale after the service restart; no browser action was
   attempted while the Chrome control connection was unavailable.
3. Run the fresh 500-target slot-1 pacing/performance gate and record measured
   stage mean/P90 separately from projections.
4. Complete the remaining Multi-account Browser Task 6 verified post-follow
   welcome, automatic reply,
   reload-idempotency, channel-preference, single-destination invitation,
   contact-stage, human-handoff, and fresh follow-back live gates. Mutual follow,
   bidirectional manual DM delivery, and `4/4` browser health already passed; do
   not repeat them on a conversation whose later bubbles disappear after reload.
   Continue with a fresh controlled conversation that receives live DOM updates.
5. Execute the remaining Mobile Task 10 two-device live gate on slots 1 and 2.
6. Finish full regression, two-device calibration, four-/eight-hour
   endurance tests, seven-device benchmark, runbooks, branch integration, and
   GitHub setup.

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
