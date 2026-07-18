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

## Checkpoint (2026-07-18)

Completed on `feat/web-lead-conversion`:

- Web Task 1: browser-only account configuration.
- Web Task 2: durable reply plans and exclusive browser action leases.
- Web Task 3: reviewed lead-conversation policy with bilingual handoff,
  monotonic stages, invitation cooldown, contact priority, and prompt limits.
- Mobile Tasks 1-9: stable target identity, immutable pools, deterministic
  rounds, shared qualification snapshots, independent quota-controlled plans,
  durable workers, semantic Appium verification, MYT discovery, proxy relay,
  fenced fleet ownership, acquisition CLI operations, and strict capacity and
  coverage gates.
- Task 9 implementation commit: `b22bf69`
  (`feat: operate and gate acquisition capacity`).
- Latest code commit: `e58080c`
  (`fix: harden capacity and fleet shutdown gates`).
- Task 9 repeated specification and quality reviews passed with no Critical,
  Important, or Minor findings after the final lease-release fixes.
- Last fresh full Python verification: `402 passed`.
- Last focused Task 9 verification: `162 passed`.
- Ruff check, focused format check for all seven Task 9 review-fix files, and
  `git diff --check` passed.
- Read-only MYT discovery found running slot 1 at ADB/web ports `30000/30001`
  and slot 2 at `30100/30101`. No account action was performed.
- Full-repository Ruff format check has a pre-existing 14-file formatting
  baseline; do not reformat those unrelated files as part of a narrow task.

Outstanding at the current checkpoint:

1. Continue Web Tasks 4-10 and the operator-console plan in roadmap order.
2. Execute Mobile Task 10 only at the roadmap's two-device live gate.
3. Finish full regression, two-device calibration, four-/eight-hour
   endurance tests, seven-device benchmark, runbooks, branch integration, and
   GitHub setup.

## Next Execution Procedure

1. Enter the active feature worktree and read this file plus both current plans.
2. Run `git status --short --branch` and `git log -5 --oneline`.
3. Preserve any new user changes and inspect them before proceeding.
4. Read Web Task 4 plus the current messaging client and tests before editing.
5. Add the prompt-context test, run it to observe the expected failure, and
   implement only enough behavior to pass the red-green slice.
6. Repeat focused and full verification plus specification and quality review
   before committing Web Task 4.

If runtime state differs from this checkpoint, report the concrete difference,
update the checkpoint, and continue from the latest verified state.
