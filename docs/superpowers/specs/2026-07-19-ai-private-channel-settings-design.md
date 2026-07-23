# AI Provider And Private-Channel Settings Design

**Date:** 2026-07-19  
**Status:** Approved for specification review  
**Scope:** Management settings, autonomous inbound replies, follow-back policy,
private-channel invitations, and a future Supabase persistence boundary.

## Goal

Give the operator one localhost management surface for configuring an
OpenAI-compatible provider and per-account sales behavior, then use those
settings to follow back new followers and reply to inbound TikTok messages in a
timely, concise, account-isolated way. The conversation should answer the lead
first and invite qualified buyers to one preferred private channel without
repeated or premature contact promotion.

## Decisions

The system uses one global AI provider configuration and one sales configuration
per browser account.

- Global provider fields are base URL, API key, and model.
- Per-account fields are WhatsApp destination, Telegram destination, offer
  facts, FAQ facts, reply tone, AI reply enabled, and follow-back enabled.
- The two current accounts may share the same destinations while retaining
  separate switches and sales context.
- Duplicate destinations entered by the operator are normalized as one value.
- SQLite remains the runtime store for this acceptance cycle. New persistence
  access stays behind repository/service boundaries so operational records can
  later move to Supabase Postgres without changing browser or console contracts.
- Provider API keys remain server-side secrets. A later Supabase deployment
  must use server-side secret storage rather than exposing keys in browser rows
  or client-readable tables.

This split avoids duplicating provider credentials for every TikTok account and
still permits account-specific products, language tone, contacts, and controls.

## Management Console

Add a fourth top-level view, `设置`, at `/settings`. It contains two unframed
sections.

### AI Service

The global form contains:

- OpenAI-compatible base URL.
- Write-only API key input.
- Model name.
- `保存` command.
- `测试连接` command.

After a key is saved, the API returns only `key_configured: true`; it never
returns a masked or partial key. Leaving the key field empty preserves the
stored key. A separate explicit clear command removes it. The connection test
returns only success/failure, model, and elapsed milliseconds. It does not
return generated content or credentials.

The base URL must be HTTPS, except loopback HTTP used by local fixtures. It is
normalized without a trailing slash. The model must be a non-empty bounded
identifier. Saving invalid fields returns field-level validation errors and
does not replace the last working configuration.

### Account Automation

Render one settings row per configured browser account with the Profile label,
expected TikTok username, AI reply switch, follow-back switch, WhatsApp,
Telegram, offer facts, FAQ facts, and concise reply-tone instructions. Contact
inputs are treated as sensitive local configuration and are never echoed into
readiness, logs, reports, or analytics. The console may display locally loaded
values only on the settings page; general account APIs expose booleans such as
`private_channel_configured`.

Saving an account affects only that account. Missing destinations disable
private-channel invitations for that account but do not disable ordinary AI
answers. Follow-back and AI reply switches remain independent.

## Secret And Configuration Storage

Provider credentials and private-channel destinations live in ignored local
configuration with owner-only permissions. The service writes the file
atomically and uses mode `0600`. Committed examples contain only synthetic
placeholders.

The runtime configuration service merges:

1. saved localhost settings;
2. existing environment variables as a compatibility fallback; and
3. committed non-secret account defaults.

Saved settings take effect for new plans without restarting the service. An
already persisted reply plan remains immutable and continues to use its stored
text. No endpoint, exception, health payload, test output, checkpoint, or commit
contains a key, destination, message body, cookie, token, or authenticated
browser state.

Settings mutations are accepted only from loopback console requests with the
same origin and JSON media type checks already used by operator commands.

## Conversation Policy

### New Follow

When follow-back is enabled, a newly observed follower receives one leased and
visibly verified follow-back action. The system does not send an unsolicited DM
because of the follow alone. Reloads and repeated observations reuse durable
identity and action evidence rather than creating another click.

### New Inbound Message

When AI reply is enabled, the server assesses the inbound message, persists one
immutable reply plan, and grants one exclusive visible-send lease. The reply:

- uses the sender's language;
- answers the actual question before qualifying;
- remains concise;
- asks at most one qualifying question;
- uses only configured offer and FAQ facts; and
- stays within the existing 12-reply autonomous budget.

### Private-Channel Invitation

The system considers an invitation due after a buying signal or the second
meaningful inbound turn. Before a preference is known, the reply asks whether
the lead prefers WhatsApp or Telegram and does not include both destinations.
After the lead chooses, the next eligible reply provides only that channel and
ends with a short, natural buying-oriented call to action. For example, the
intent is: interested buyers can contact us there for details or purchasing;
the model adapts the exact sentence to the sender's language and context.

Each conversation has a 24-hour invitation cooldown. Existing invitation
evidence, contact capture, or explicit channel acceptance suppresses repeated
promotion. A provided contact advances the conversation to
`contact_captured`, acknowledges it briefly, and moves toward closing.

### Human Takeover

Payment, refunds, complaints, cancellations, unsupported promises or discounts,
and explicit requests for a person advance the conversation to
`human_required`. The automation creates no further AI send plan until an
operator resolves the takeover state.

Stages remain monotonic: `new`, `engaged`, `qualified`, `invited`,
`contact_captured`, `human_required`, `closed`.

## Data Flow

1. The extension observes visible Activity or Messages state and submits an
   account-bound event.
2. The localhost API verifies Profile binding and visible TikTok identity.
3. The conversion service loads per-account automation settings through the
   configuration repository.
4. Follow events may create one follow-back lease. Inbound message events may
   create one assessment and immutable reply plan.
5. The AI provider is called only when a new plan is needed. The private
   destination is added to the prompt only after policy selects a specific
   channel.
6. The extension claims one exclusive action, performs the visible UI action,
   and reports the visible result.
7. Persistence records stage, cooldown, plan, lease, and funnel evidence without
   retaining sensitive settings in analytics responses.

## Supabase Migration Boundary

The first Supabase phase will replace operational SQLite repositories with
Postgres-backed implementations for accounts, conversations, plans, leases,
funnel events, and sales. API response models, browser event contracts, policy
services, and console components remain unchanged.

Required properties for that phase are:

- account-scoped row ownership and indexes;
- unique constraints for inbound fingerprints and exclusive actions;
- transactional compare-and-set lease transitions;
- monotonic stage updates;
- service-role-only writes for automation;
- redacted analytics views; and
- migrations runnable through the installed Supabase CLI.

Supabase work is recorded as the next persistence project, not mixed into this
live browser acceptance change. This keeps the current two-account workflow
stable while ensuring new settings code does not depend directly on SQLite
details.

## Failure Handling

- Provider timeout, invalid response, or connection failure uses the configured
  bounded fallback acknowledgement and records only a non-sensitive error code.
- A failed settings test does not disable or overwrite the last saved provider.
- A visible action reported `uncertain` remains busy until reconciliation or
  lease expiry; it is not immediately retried.
- Signed-out, verification-required, username-mismatch, stale-heartbeat, or
  unbound browser states block follow and send claims.
- Missing channel preference causes another ordinary helpful response or a
  preference question, never disclosure of both destinations.
- Restricted TikTok conversations remain conversation-specific failures and do
  not mark the whole account messaging path unhealthy.

## Verification

### Automated

- Configuration repository tests cover atomic writes, permissions, precedence,
  validation, empty-key preservation, explicit clearing, and redaction.
- API tests cover loopback/origin/media-type checks, settings reads and writes,
  connection testing, account isolation, and absence of secrets in responses.
- Policy tests cover first-turn answers, buying signals, second meaningful
  turns, channel preference, single-destination disclosure, cooldown,
  contact capture, human takeover, reply budget, and immutable plan reuse.
- Console tests cover `/settings`, provider status, field validation, per-account
  saves, responsive layout, keyboard focus, and no secret rendering after save.
- Full Python, Chrome extension, frontend, Android build, Ruff, production
  build, and `git diff --check` verification run before completion.

### Real Two-Account Acceptance

Use two controlled, message-capable TikTok accounts with Activity and Messages
health ready for both Profiles.

1. Establish follower baselines before enabling follow-back.
2. Enable follow-back and verify exactly one visible follow plus no duplicate
   after reload.
3. Send a controlled inbound message and verify one AI plan, one lease, one
   visible reply, and no duplicate after reload.
4. Exercise a buying signal or second meaningful inbound turn and verify a
   channel-preference question.
5. Choose one channel and verify only the selected destination appears once.
6. Verify cooldown/idempotency, contact capture, and human takeover.
7. Record only redacted status and identifiers in the checkpoint.

Restricted recipients are excluded from visible-send proof; their uncertain
leases remain preserved for reconciliation. Existing mutual follow and manual
DM evidence is not repeated.

## Completion Criteria

The feature is complete when the localhost management console can safely save
and test the global provider, independently configure both accounts, drive the
approved follow-back and reply policy, pass automated verification, and pass the
remaining visible two-account gates with redacted checkpoint evidence. The
Supabase migration is explicitly queued with a stable repository contract and
is not reported as completed in this cycle.
