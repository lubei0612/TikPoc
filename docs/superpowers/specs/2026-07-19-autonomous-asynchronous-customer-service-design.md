# Autonomous Asynchronous Customer Service Design

## Status And Scope

This design supersedes the browser-conversation behavior that requires a live
human takeover. It keeps the existing account isolation, visible-DOM actions,
durable reply plans, action leases, reply budgets, and funnel accounting.

The goal is a stable, unattended browser loop while Chrome remains running:

1. Detect a new follower for the bound TikTok account.
2. Follow back once and verify the visible following state.
3. Send one verified welcome that asks whether the follower is interested in
   the configured mirror-quality bags.
4. Detect new inbound messages and answer them with the configured AI provider.
5. Direct interested customers to the link on the account profile or the
   contact details in pinned profile posts.
6. Stop all automated contact after an explicit refusal or stop-contact request.

Navigation buttons and broad browser-fleet navigation are outside this change.
Both TikTok Messages route families remain supported because TikTok may redirect
accounts between `/messages` and `/business-suite/messages`.

## Customer-Service Policy

### New-follower welcome

A welcome plan is created only after an account-scoped follow-back lease reaches
`completed` and the visible button state confirms the follow. The welcome is
idempotent by account and normalized follower username.

The welcome must:

- use the configured default welcome language when no customer language exists;
- identify the configured brand AI customer-service role in one short clause;
- thank the person for following;
- ask one direct question about interest in the configured mirror-quality bags;
- mention that the customer can reply at any time;
- avoid prices, stock, delivery promises, discounts, payment instructions, and
  direct contact values that are not in configured facts;
- avoid repeated follow-ups when the customer has not replied.

Recommended English structure, generated rather than hard-coded:

> Thanks for following IKUN. I'm IKUN's AI customer-service assistant. Are you
> interested in our mirror-quality bags?

The configured brand name and offer facts remain the source of truth. Tests use
synthetic brand and product fixtures.

### Inbound AI replies

For each fresh inbound fingerprint, the server creates at most one immutable AI
reply plan. Replies use the sender's language, answer the current question
before qualifying, contain one to three short sentences, and ask at most one
question.

The AI owns the asynchronous conversation. It does not promise a live transfer
or claim that a person is currently available. Requests that previously entered
`human_required` are handled as follows:

- ordinary product, availability, shipping, and ordering questions receive only
  answers supported by configured offer and FAQ facts;
- payment, refund, cancellation, complaint, unsupported promise, discount, or
  explicit representative requests receive a concise limitation statement and
  the profile-contact route described below;
- the reply must not invent a decision, resolution time, or human follow-up.

### Interest and contact route

Buying intent or the configured meaningful-turn threshold makes the contact
route due. The AI tells the customer to use the link on the TikTok account
profile or the contact details in the account's pinned profile posts. It does
not ask the customer to choose WhatsApp or Telegram and does not disclose a
stored direct destination in the automatic reply.

The 24-hour invitation cooldown remains. Contact information sent by the
customer is captured durably and acknowledged, but it does not trigger a human
takeover promise.

### Stop-contact behavior

Explicit refusal, hostility combined with a stop-contact instruction, or an
unambiguous request not to follow or message must produce an empty terminal
plan. Examples include `stop messaging me`, `do not contact me`, `leave me
alone`, and equivalent supported-language phrases.

The conversation is marked `closed` with a durable stop-contact reason. Pending
welcome plans for the same account and participant are superseded. No AI call,
send lease, follow-up, invitation, or further automatic reply is permitted.
General negative sentiment without a stop instruction is not enough to close a
conversation.

## Continuous Observation

Continuous means that Chrome and the dedicated Profile remain running and the
TikTok account remains visibly signed in and free of verification challenges.
It does not imply operation while Chrome is closed or the computer is asleep.

Activity and Messages observers use the same recovery contract:

- scan after initial binding, relevant DOM mutation, storage-policy change,
  route change, visibility change, and background health tick;
- run a bounded periodic watchdog even when TikTok does not push a DOM mutation;
- serialize scans per content script so watchdog and mutation triggers cannot
  overlap;
- re-evaluate the visible account identity before every action-bearing request;
- report page role, binding state, observed username, last scan time, and last
  successful observation to localhost health persistence;
- mark health stale when scans stop, rather than treating an open tab as healthy;
- preserve startup baselines so historical rows do not cause bulk replies;
- process fresh rows after the baseline and retain account-scoped processed
  fingerprints across reloads and DOM rerenders.

The Activity watchdog may reopen the Activity panel when automatic follow-back
is enabled and the panel has closed. The Messages watchdog supports both
`/messages` and `/business-suite/messages`, including query strings, trailing
slashes, conversation subpaths, same-document route changes, and nested frames.

## Safety And Idempotency

Every follow-back, welcome, and reply requires an exclusive account-scoped
action lease. Visible completion is the only success signal. An `uncertain`
result remains busy until reconciliation or lease expiry.

The system never reads cookies, session storage, browser credential databases,
or TikTok tokens. Logs and committed evidence exclude message bodies, contact
values, private destinations, and screenshots containing personal data.

## Acceptance Contract

Automated acceptance must prove:

1. Both Messages route families and their query/subpath variants load the DM
   observer.
2. Mutation, watchdog, visibility, route, storage, and background tick triggers
   coalesce into serialized scans.
3. Startup history becomes a baseline, while a later fresh message creates one
   plan and one send lease.
4. Reload and DOM rerender do not duplicate a welcome, follow-back, AI plan, or
   visible send.
5. Completed follow-back creates one product-interest welcome.
6. Buying intent produces the profile-link/pinned-post contact route without a
   direct stored destination.
7. Former human-takeover inputs receive a bounded profile-contact reply without
   a transfer promise.
8. A stop-contact message calls no AI provider and creates no actionable reply.
9. Equal follower and message identifiers remain isolated across two accounts.

Live acceptance uses two controlled Profiles and records only redacted evidence:

1. Activity and Messages health are ready and fresh for both accounts.
2. A fresh follow produces one visibly confirmed follow-back and one welcome.
3. A fresh inbound message produces one visible language-matched AI reply while
   the receiving tab is in the background.
4. A buying-interest message receives the profile-link or pinned-post route.
5. Reloading both pages produces no duplicate action.
6. A fresh stop-contact fixture produces no visible outbound reply.

The live gate is not complete from unit tests, a health heartbeat, an HTTP 200,
or a DOM click alone.
