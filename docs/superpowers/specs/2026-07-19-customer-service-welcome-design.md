# Customer-Service Reply And New-Follower Welcome Design

**Date:** 2026-07-19  
**Status:** Approved by the operator  
**Scope:** IKUN brand context, customer-service reply behavior, and one welcome
DM after a verified browser follow-back.

## Goal

Make autonomous replies feel like a warm, professional brand service desk and
contact a new follower while their interest is fresh. The complete browser flow
is: observe a new follower, visibly verify one follow-back, send one concise
welcome DM, continue the conversation in the sender's language, and invite a
qualified buyer to one selected private channel.

This document supersedes only the `New Follow` rule in
`2026-07-19-ai-private-channel-settings-design.md`. All identity, lease,
conversation-stage, invitation, human-takeover, and secret-handling rules in
that design remain in force.

## Account Configuration

The existing account automation form gains:

- `brand_name`: the public brand used in the AI introduction;
- `welcome_after_followback`: an explicit account-level switch; and
- `welcome_language`: the language used when a follower has not written any
  text from which language can be detected.

The current operator value for `brand_name` is `IKUN`. The committed code and
fixtures do not contain real contact destinations, credentials, or message
bodies. Brand, welcome, offer, FAQ, and tone settings remain account-scoped,
locally stored, atomically written, and hot-loaded for new plans.

`welcome_language` is a short language name such as `English` or `Chinese`, not
an unrestricted prompt. Once the customer writes, their current language takes
priority over this default.

## Customer-Service Reply Contract

The model follows a four-part service rubric without mechanically printing
four sections:

1. **Acknowledge:** refer to the customer's actual item, question, or intent.
2. **Assist:** provide the most useful confirmed answer before qualifying.
3. **Advance:** ask at most one low-effort question, preferably a short set of
   choices when clarification is needed.
4. **Assure:** state the next step briefly and close warmly.

Replies normally contain one to three short sentences. They avoid generic
openers, repeated thanks, multiple questions, unsupported promises, aggressive
sales language, and immediate contact-detail dumping. A light emoji is optional
only in friendly conversations; complaints, payments, refunds, cancellations,
and human handoff use no emoji.

The first autonomous message in a conversation introduces itself once as the
brand's AI customer-service assistant. Later replies do not repeat the AI
introduction. The disclosure is concise and is never used to avoid answering
the customer's question.

Configured offer and FAQ facts remain the only source for prices, inventory,
shipping, customization, minimum order quantity, discounts, payments, refunds,
links, and contact details. Missing facts produce one clear clarifying question
or human takeover rather than an invented answer.

## New-Follower Welcome Flow

The previous follow-only behavior is replaced with this account-scoped flow:

1. The Activity observer establishes its existing baseline and detects a new
   follower with a normalized username and stable follower key.
2. It claims and visibly verifies exactly one `followback` action.
3. A completed follow-back creates at most one durable welcome plan for the
   normalized `(account_id, follower_username)` pair when
   `welcome_after_followback` is enabled.
4. A healthy Messages observer claims the welcome plan, locates or creates the
   exact conversation for that username, verifies the active participant, and
   sends the stored text through the visible composer.
5. The observer verifies the exact outbound bubble before recording `sent`.
   Ambiguous identity, unavailable composer, platform restriction, or missing
   visible evidence records `uncertain` and does not immediately retry.

The welcome text uses `welcome_language`, thanks the follower, introduces the
brand AI assistant, and asks one easy product-oriented question. It contains no
private-channel destination and makes no unconfigured commercial promise.

An existing inbound or outbound conversation suppresses the generic welcome.
Repeated Activity rows, reloads, extension restarts, duplicate result delivery,
and repeated claims reuse durable plan/action evidence. A database uniqueness
constraint is authoritative; extension-local processed state is only an
optimization.

Inbound replies have priority over welcome work. A Messages scan processes an
actionable inbound message first and attempts at most one welcome plan only
when no inbound candidate is waiting.

## Data And API Boundaries

A `browser_welcome_plans` table stores only bounded operational fields: account,
normalized follower username, follower key, exact planned text, state, and
timestamps. States are `planned`, `sent`, `uncertain`, and `superseded`.

The browser API adds account-bound endpoints to claim the next welcome and
record its result. Both pass through the existing visible-username binding
gate. The send uses a separate `welcome_send` browser action lease so it cannot
collide with ordinary inbound `dm_send` work. No API returns settings secrets or
another account's plan.

The follow-back result handler creates a welcome only after the matching
`followback_completed` event and completed action evidence agree on the account
and follower key. Unresolved or uncertain follow-backs create no welcome.

## Console Behavior

The settings page keeps the existing quiet operational layout. Each account
fieldset shows brand name and default welcome language near its reply context,
plus a checkbox labeled `回关后发送欢迎私信`. Saving affects one account and
preserves all omitted defaults during migration from older local settings.

The product and FAQ text areas are where the operator supplies product category,
customization, minimum order quantity, delivery, logistics, and common-answer
facts. The reply-tone field holds the approved direction: warm, professional,
concise brand customer service.

## Failure Handling

- Missing provider configuration uses a bounded, professional fallback that
  identifies the AI service role and asks one useful question.
- Empty brand name omits the brand rather than inventing one.
- Missing welcome language defaults to English for follow-only contacts.
- An unavailable or ambiguous target conversation leaves the plan unresolved;
  it does not send into the currently open conversation.
- A visible send without exact outbound confirmation is `uncertain` and remains
  busy until reconciliation or lease expiry.
- Existing inbound-message budget, invitation cooldown, contact capture, and
  human-takeover rules remain unchanged.

## Verification

Automated tests cover settings migration and API isolation, first-turn AI
disclosure, customer-service prompt structure, later-turn non-repetition,
welcome-plan uniqueness, completed-follow gating, welcome claim/result
transitions, exact participant targeting, inbound priority, visible-send
reconciliation, and reload idempotency.

The live two-account gate requires one fresh controlled follower per account.
For each direction, verify one visible follow-back, one welcome message, exact
participant identity, no duplicate after reload, and ordinary AI continuation
after a reply. Record only redacted counts and states in `AGENTS.md` and the
current browser acceptance plan.

