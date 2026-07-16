# TikTok Web Lead Conversion Design

## 1. Goal

Build a lead-acquisition system with two independent execution planes:

- Seven mobile accounts perform profile-touch work against the same target pool.
- Seven matching TikTok Web sessions handle new followers and inbound direct
  messages while the mobile workers continue uninterrupted.

The web sessions follow back through the visible Activity UI and conduct
multi-turn AI conversations through the visible Messages UI. The conversation
goal is deliberately narrow: answer basic questions, identify genuine interest,
and move the prospect to the configured private channel for human closing.

The business result is measured as a funnel, not only as raw task throughput:

`7/7 touched -> followed -> inbound DM -> engaged -> invited -> contact captured -> sale`

## 2. Decisions And Scope

### Included

- One dedicated Chrome Profile per TikTok account.
- One pinned Activity tab and one pinned Messages tab per profile.
- DOM mutation observation and semantic element matching in the existing
  Manifest V3 extension.
- Visible Follow/Follow back, textbox, and Send interactions.
- Durable inbound-message deduplication and reply-plan persistence in SQLite.
- Multi-turn, language-matched AI replies with bounded conversation history.
- Configurable product context, FAQ context, tone, private-channel destination,
  and human-handoff rules.
- Account-scoped funnel events and conversion reporting.
- A complete target-by-device coverage view for the seven mobile accounts.

### Excluded From The Production Path

- Android Inbox or Android notification discovery.
- TikTok Business Messaging credentials, webhook delivery, or message API calls.
- Browser cookie export, Chrome profile inspection, or replay of undocumented
  private requests.
- Coordinate clicks in the web bridge.
- Account login, CAPTCHA handling, and account creation.

The existing Business Messaging implementation remains dormant compatibility
code. The Chrome DOM path is the configured production default.

## 3. System Architecture

### Mobile Touch Plane

The existing `tasks` uniqueness rule, `(batch_id, target_id, device_id)`, is the
coverage ledger. Importing seven device IDs creates seven recoverable tasks for
every target. A target counts as fully covered only when all seven assignments
record a confirmed profile visit. A completed interaction and a rule-based
trace-only outcome both qualify after navigation is confirmed. A navigation
failure, identity mismatch, pending retry, or terminal execution failure does
not qualify.

Profile metrics are imported and evaluated centrally. Mobile workers receive the
precomputed eligibility decision and perform only navigation plus the planned
trace or interaction action. They do not reopen Inbox and do not reread metrics
that already exist in the source dataset.

### Chrome Conversion Plane

Each Chrome Profile is bound to exactly one `account_id` and matching
`device_id`. The extension has two page roles:

- `activity`: establish a historical baseline, detect a new follower row, claim
  the action, click the semantic Follow control, and verify the state change.
- `messages`: establish a historical baseline, detect a newly received message,
  request a durable AI reply plan, send it through the visible composer, and
  verify the outbound bubble.

TikTok Web maintains its own live page connection. The extension reacts to DOM
changes with `MutationObserver`; it does not refresh or scan the Android app.
Small timers are used only to debounce DOM rerenders and retry a failed local
request.

### Local Conversion Service

The dashboard remains bound to `127.0.0.1`. It validates the Chrome account
mapping, stores normalized browser events, manages conversation history, calls
the configured AI provider, and returns idempotent reply plans. Mobile queues and
browser queues remain separate.

The LaunchAgent continues running the dashboard and queue worker as one process
against `/Users/Shared/TikPoc/tikpoc.db`.

## 4. Browser Account Runtime

The seven-account deployment uses seven Chrome Profiles, each signed into the
same TikTok account used by its paired mobile device. Every profile loads the
unpacked `chrome-event-bridge` and stores only:

- `account_id`
- `device_id`
- local dashboard URL
- enabled page roles

The background service worker keeps an account-scoped action lease. Duplicate
tabs may observe the same DOM row, but only the holder of the lease receives a
claim to click or send. A page reports a lightweight health event on load and
after each completed action. A 60-second extension alarm reports tab presence and
page role without reading Inbox contents, so the dashboard can show which
accounts are ready.

The production extension is distinct from the ChatGPT Chrome Extension. The
latter is used only for selector calibration and acceptance inspection.

## 5. Follow-Back Flow

1. The Activity tab waits for its panel to become visible and records all
   currently visible follower rows as the baseline.
2. A later DOM mutation schedules a debounced semantic scan.
3. A candidate requires all of these signals: a follower phrase, a canonical
   TikTok profile link, and an exact Follow or Follow back button label.
4. The extension requests an account-scoped action claim from the dashboard.
5. The claim winner records `new_follower`, clicks once, and waits for the button
   to change to a completed state such as Following or Friends.
6. The extension records `followback_completed` or `followback_unresolved`.

Historical rows, ambiguous rows, and duplicate observations produce no click.
The preferred deduplication identity is account, normalized username, and a
DOM-provided Activity event identity. When TikTok exposes no event identity, the
fallback is account plus normalized username and later refollows remain manual.

## 6. Direct-Message Flow

### Detect And Plan

1. The Messages tab records the visible conversation rows and their latest
   messages as its startup baseline.
2. A DOM mutation identifies an unread or changed conversation row.
3. The extension opens one conversation at a time and extracts the participant,
   stable conversation URL or key, latest visible message direction, text,
   visible timestamp, and any DOM-provided message identity.
4. It builds a SHA-256 fingerprint from the account, conversation key, sender,
   visible message identity, timestamp, and normalized text.
5. Only a latest inbound message is submitted to
   `POST /api/browser-dm/reply-plan`.
6. The server inserts the inbound message and reply-plan identity transactionally.
   Repeated fingerprints return the previously stored plan.
7. The server loads bounded conversation history, advances the conversion state,
   generates one reply, persists the exact draft, and returns it.

The reply-plan request is event-driven and stays open while the AI draft is
generated. This avoids an Inbox polling loop and avoids a second extension
command channel. A bounded retry uses the same fingerprint and therefore receives
the same draft.

### Verify And Send

1. Before editing the composer, the extension re-reads the active conversation.
2. The account, conversation key, and latest inbound fingerprint must still match
   the reply plan.
3. The extension focuses the visible textbox, applies the native input value,
   emits the input events TikTok expects, and verifies the composed text.
4. It clicks the visible semantic Send control once.
5. It waits for an outbound bubble whose normalized text matches the planned
   reply.
6. It posts `sent`, `uncertain`, or `superseded` to
   `POST /api/browser-dm/reply-result`.

An uncertain send is reconciled against visible outbound bubbles before the
server releases another plan. This prevents duplicate replies after navigation,
rerenders, extension reloads, and local service restarts.

## 7. Conversation State Machine

Each conversation has one of these states:

- `new`: first inbound message received.
- `engaged`: AI is answering basic questions and asking one concise qualifying
  question at a time.
- `qualified`: the prospect expresses product, price, availability, shipping, or
  purchase interest.
- `invited`: the configured private-channel destination has been offered.
- `contact_captured`: a usable contact or explicit private-channel acceptance is
  visible in the conversation.
- `human_required`: payment, complaint, refund, custom promise, or other closing
  decision needs the operator.
- `closed`: the maximum dialogue budget is reached or the prospect ends the
  conversation.

One inbound message creates at most one outbound reply. The default autonomous
budget is 12 replies per conversation. The private-channel invitation is used
after a buying signal or after two meaningful inbound turns, at most once in a
24-hour window. Once contact is captured, the AI acknowledges it and marks the
lead for human closing instead of repeating the invitation.

The AI receives:

- the sender's recent messages and the account's recent replies;
- account-specific product and FAQ context;
- the current funnel state;
- the configured private-channel destination;
- instructions to use the sender's language and keep replies concise.

The AI may answer from configured facts. Prices, inventory, delivery promises,
refund decisions, and payment instructions require explicit configured facts or
human handoff. If the AI service has a transient error, the account may send one
configured acknowledgement template and preserve the conversation for a later
turn.

## 8. Configuration

`config/web-accounts.yaml` is extended with nonsecret conversion settings:

```yaml
accounts:
  - account_id: account-01
    device_id: phone-01
    mode: "browser"
    private_channel_hint: "WhatsApp: +1 555 0100"
    offer_context: "Bags from the current account catalog"
    faq_file: "config/account-01-faq.md"
    reply_language: "auto"
    max_auto_replies: 12
    invite_after_meaningful_turns: 2
    fallback_acknowledgement: "Thanks for your message. What are you looking for?"
    browser_followback_enabled: true
    browser_dm_enabled: true
    enabled: true
```

`mode: browser` makes Business Messaging identifiers and token files optional.
The registry continues validating those fields for accounts explicitly set to
Business API mode.

The production configuration contains the real private-channel destination. If
that field is empty, the account remains in basic-reply mode and records an
`invite_configuration_missing` event instead of inventing contact information.

## 9. Persistence

The existing `web_events`, `web_conversations`, and `web_messages` tables remain
the base. Additive migrations add:

- conversation state, turn counters, invitation timestamp, contact-captured
  timestamp, and human-handoff flag;
- `browser_reply_plans`, unique by account and inbound fingerprint, with exact
  draft text and `planned`, `sent`, `uncertain`, or `superseded` state;
- `lead_funnel_events`, deduplicated by account, prospect, stage, and source event;
- browser account health, last page role, last successful action, and last error.

Message text and captured contact data remain in the local SQLite runtime. Logs
contain event identities, state changes, durations, and error categories rather
than full message bodies or API keys.

## 10. Throughput And Revenue Measurement

Ten thousand unique targets with seven-account coverage means 70,000 mobile
visits per day. Each device must complete 10,000 visits, which allows:

- 8.64 seconds per target over 24 hours;
- 7.20 seconds per target over 20 hours.

The production touch path is promoted only after every device sustains an average
below 6.5 seconds and a p90 below 8.64 seconds on a representative batch. Deeper
video actions remain rule- and quota-controlled; they may not force the broad
coverage path above its latency budget.

The dashboard reports by account and batch:

- unique targets and 7/7 coverage rate;
- follower count and follower conversion rate;
- inbound conversations and DM conversion rate;
- engaged and qualified leads;
- private-channel invitations and captured contacts;
- human handoffs, manually recorded sales, revenue, and revenue per 1,000 fully
  covered targets;
- median and p90 time from inbound message to confirmed reply.

Optimization decisions use private-contact and sale conversion, not raw clicks.
The first operating target is a confirmed reply latency under 60 seconds while a
signed-in Messages tab is healthy.

## 11. Error Handling

- Selector ambiguity records an unresolved event with semantic diagnostics and
  leaves the page unchanged.
- A changed latest message supersedes the old reply plan before send.
- A send without a matching outbound bubble becomes `uncertain` and enters UI
  reconciliation rather than immediate resend.
- Sign-out, verification screens, and unavailable composers mark the browser
  account unhealthy and raise a dashboard alert.
- AI failures retain the inbound event and use at most one configured
  acknowledgement for that fingerprint.
- Account IDs, conversation histories, reply plans, and leases are all scoped to
  one Chrome Profile mapping, preventing cross-account replies.
- Browser failures never pause or preempt mobile trace tasks.

## 12. Testing And Acceptance

### Automated Tests

- Pure JavaScript tests cover follower phrases, conversation identities, inbound
  direction classification, message fingerprints, button labels, reply-plan
  deduplication, and send reconciliation.
- Synthetic DOM adapter fixtures cover unread rows, active threads, contenteditable
  composers, rerenders, ambiguous controls, and matching outbound bubbles.
- Python tests cover browser account validation, reply-plan idempotency,
  conversation transitions, contact detection, invitation cooldown, AI failure,
  result reconciliation, and funnel aggregation.
- Component tests run the dashboard with a fake AI client and exercise the full
  inbound-plan-result sequence.
- Existing mobile, dashboard, database, worker, lint, and extension tests remain
  green.

### Live Acceptance

For each configured account, use a second controlled TikTok account to perform:

1. Follow the target account and verify one follow-back action.
2. Send a first DM and verify one language-matched AI reply.
3. Continue for at least three inbound turns and verify bounded history and state
   progression.
4. Provide a private-channel contact and verify `contact_captured` plus human
   handoff.
5. Reload the Messages page and verify no historical message is resent.
6. Rerender the active thread and verify duplicate DOM observations create no
   duplicate reply.
7. Keep the paired mobile worker active throughout and verify its current target
   advances independently.

The web conversion path is production-ready for an account only after all seven
checks pass in its dedicated Chrome Profile. The 10,000-target claim is enabled
only after the seven-device sustained throughput gate passes on a fresh target
dataset.
