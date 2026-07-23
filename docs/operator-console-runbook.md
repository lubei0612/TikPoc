# TikPoc Operator Console Runbook

## Start The Console

Build the embedded console after changing React code:

```bash
npm ci --prefix operator-console
npm run build --prefix operator-console
```

Start the localhost service with a local database and account registry:

```bash
uv run tikpoc serve \
  --db var/tikpoc.db \
  --web-accounts config/web-accounts.yaml \
  --port 8765
```

Open `http://127.0.0.1:8765/operations`. Add `--with-web-worker` only when the
browser lead worker should run in the same process. The Chrome extension keeps
using the existing localhost API routes on this port. The historical
`tikpoc dashboard` command remains a compatibility alias.

## Multi-Account Readiness

Each enabled entry in `config/web-accounts.yaml` needs a unique `account_id`, a
unique paired `device_id`, browser mode, and its own Chrome profile outside the
repository. Configure the offer/FAQ facts and private-channel hint locally.
The console exposes only readiness booleans; it does not return the configured
destination or credentials.

Before processing leads, confirm:

- the Operations header reports the expected dynamic device and browser counts;
- every account/device mapping is the intended pair;
- the Inbox readiness strip shows `Private ready` for accounts that may invite;
- AI and follow-back switches match the intended operator policy;
- browser observers report recent `activity` or `messages` health.

## Pools And Rounds

Import a deduplicated target pool and create a round with the intended device
configuration:

```bash
uv run tikpoc pool-import --db var/tikpoc.db --csv imports/targets.csv
uv run tikpoc round-create \
  --db var/tikpoc.db \
  --pool POOL_ID \
  --devices config/settings.yaml \
  --starts-at 2026-07-18T09:00:00+08:00
```

Select the round in the console. Fleet and round start/pause/stop commands are
idempotent server commands. A stopped round remains terminal. Device rows show
health and current assignment state; controls apply only where the worker has a
truthful control path.

## Quotas, Retry, And Diagnostics

Natural-hour quota rows show limit, reserved, confirmed, uncertain, remaining, and
reset time per account/device and outcome. Reserved work consumes remaining
capacity until it reaches a verified terminal state.

Use Retry only on a deferred assignment. The server validates the assignment
state and records the command before making it retry-ready. Expand Diagnostics
to read the latest bounded UI summary and locally served image evidence. A
successful tap, click, or HTTP response alone is not completion evidence.

## Mobile Trace And Coverage

Target Coverage is the durable account-by-target matrix. `N/N` is computed from
the round's device set, so the console supports any configured multi-account
count. The sticky target column remains visible while the account columns
scroll horizontally on narrow screens.

Runtime Evidence lists recent mobile visit confirmations separately from
completed interactions. Coverage uses `visit_confirmed_at_ms`; task creation or
attempted navigation does not increase confirmed coverage.

## Private-Channel And Closing Workflow

Inbox orders durable lead conversations and shows account readiness, stage,
message age, invitation/contact signals, and bounded redacted history.

1. Open a conversation and verify its account and participant identity.
2. Use Take over when an operator should own the conversation.
3. Enter a manual reply and create the immutable send plan.
4. Treat the plan as `pending` until the visible browser send and reconciliation
   path records its result; creating a plan does not mean the message was sent.
5. Record a sale with amount, currency, and outcome only after the business
   outcome is known.

Returning a conversation to AI requires an eligible nonterminal state, account
AI enabled, and no unresolved send. Conversation stages do not regress from
terminal or escalated states.

## Brand Service And Follower Welcome

The `/settings` account form also controls the public brand name, the default
welcome language, and `回关后发送欢迎私信`. The default language is used only
when a follower has not written any text; ordinary replies follow the sender's
language. Put commercial facts in the offer and FAQ fields and keep the reply
tone focused on warm, professional customer service.

The first autonomous message introduces the configured brand's AI service role
once. Later replies acknowledge the customer's actual intent, answer with
confirmed facts, ask at most one easy question, and state the next step. Private
destinations remain hidden until the existing buying-signal and channel-choice
policy selects one channel.

When follower welcome is enabled, a welcome plan is created only after the
matching visible follow-back is confirmed. Messages opens the exact normalized
username, suppresses the generic welcome when conversation history already
exists, and requires an exact outbound bubble before recording `sent`.
`uncertain` or `superseded` plans are not immediately resent. Turn off the
welcome checkbox to pause new welcomes without disabling inbound AI replies.

## Analytics

Analytics deliberately separates measured evidence from projections:

- measured: durable confirmed visits, exact coverage, completed assignments,
  funnel events, sales, and confirmed revenue;
- projected: daily capacity derived from recorded device timing;
- promotion: additionally requires the identity, route, action, timing, and
  complete coverage gates.

A short synthetic run or unit suite is not proof of the daily production goal.

## Troubleshooting

- **Console shell loads but data fails:** check `/api/rounds`, `/api/operations`,
  and `/api/leads` on localhost; confirm the selected round still exists.
- **Blank or stale console after a frontend change:** rebuild the embedded Vite
  output and restart the service. HTML uses no-cache; hashed assets are immutable.
- **Browser health missing:** confirm the dedicated Chrome profile and extension
  observer are open on the expected Activity or Messages surface.
- **Mobile traces missing:** inspect the assignment diagnostic and verify a
  durable visible-state visit confirmation was recorded.
- **Retry rejected:** reconcile uncertain work or wait for its lease/window; only
  deferred assignments are retryable.
- **Private readiness missing:** update the ignored local account registry and
  restart. Keep destinations and keys out of logs, screenshots, and commits.
- **Manual plan remains pending:** inspect browser observer health and outbound
  reconciliation. Do not create a second plan for the same inbound fingerprint.
- **Follower was followed back but not welcomed:** confirm the account welcome
  checkbox, Messages health, exact follower username, and welcome-plan/action
  state. An ambiguous conversation or unconfirmed outbound bubble stays
  `uncertain` rather than sending into another thread.
