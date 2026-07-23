# Follow-back Circuit Breaker Design

**Date:** 2026-07-20

## Objective

Prevent one TikTok account's temporary follow restriction from causing repeated
follow attempts, while keeping its Activity observation and AI message handling
online. The circuit is account-scoped, durable across service and browser
restarts, and supports one controlled canary after the cooldown expires.

This spec is the first environment-isolation increment. Separate Chrome process
launching and stable egress assignment remain a later Supervisor increment;
their registry fields must not be coupled to the action circuit.

## Observed Failure

Both controlled accounts accepted a follow click optimistically, displayed a
temporary followed state, and returned to the unfollowed state after reload.
No new follower event, follow-back lease, or welcome plan was created. Earlier
in the same run, delayed historical notifications caused several follow-back
attempts in a short interval. This matches a temporary account-level follow
restriction more closely than an identity, health, or message transport fault.

## Alternatives

### A. Disable the global follow-back switch manually

This is operationally simple but loses the reason, expiry, and recovery state.
It also relies on an operator remembering which account is safe to resume.

### B. Retry unresolved follows with a longer delay

This risks extending a platform restriction and cannot distinguish a transient
DOM problem from an account-level follow block.

### C. Durable per-account circuit with cooldown and canary

This is the selected approach. A failed or unresolved claimed follow opens a
24-hour cooldown for that account only. Activity remains read-only, AI messages
remain enabled, and the account receives one canary claim after expiry. A
completed canary closes the circuit; another uncertain result reopens it.

## State Model

Each `(account_id, action_type='followback')` has one durable row:

```text
closed -> cooldown -> canary -> closed
                    \-> cooldown
```

- `closed`: normal account-scoped follow-back claims are allowed.
- `cooldown`: follow-back claims are rejected until `cooldown_until_ms`.
- `canary`: exactly one follow-back claim is allowed. Other claims stay queued.
- A completed canary returns to `closed`.
- An uncertain canary returns to `cooldown` for another 24 hours.
- Operator disable remains independent. The effective action gate requires both
  the operator switch and circuit permission.

## Persistence

Create `browser_action_circuits`:

```sql
CREATE TABLE browser_action_circuits (
    account_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    opened_at_ms INTEGER NOT NULL DEFAULT 0,
    cooldown_until_ms INTEGER NOT NULL DEFAULT 0,
    canary_action_key TEXT NOT NULL DEFAULT '',
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY(account_id, action_type)
);
```

The table contains no message text, cookies, tokens, proxy destinations, or
personal contact data.

## Server Boundaries

The database owns circuit transitions so claim and canary reservation can use
`BEGIN IMMEDIATE` and remain correct across processes.

Required methods:

- `browser_action_circuit(account_id, action_type, now_ms)` returns the effective
  state and promotes an expired cooldown to `canary`.
- `open_browser_action_circuit(...)` records a bounded reason and cooldown.
- `claim_browser_followback_action(...)` checks the operator switch and circuit,
  reserves the canary action key atomically, then creates the normal lease.
- `finish_browser_followback_action(...)` records the lease result and closes or
  reopens a canary circuit.

The browser action claim endpoint must use the follow-back-specific claim
method. The result endpoint accepts an optional bounded `reason`; an uncertain
follow-back result opens the circuit. DM and welcome leases keep their current
behavior.

An operator endpoint opens a circuit explicitly after a confirmed platform
rollback:

```text
POST /api/accounts/{account_id}/followback-cooldown
```

The command is idempotent by `command_id`, defaults to 24 hours, and accepts only
bounded reason codes. Enabling follow-back during an active cooldown returns a
conflict containing the cooldown expiry instead of silently clearing it.

## Extension Behavior

The Activity bridge keeps observing and establishing baselines while the
circuit or operator switch blocks actions. A claimed follow that does not reach
a completed visible state reports:

```json
{
  "state": "uncertain",
  "reason": "followback_unresolved"
}
```

The extension never retries the same uncertain record immediately. A later
reload rollback that cannot yet be detected automatically remains covered by
the operator cooldown endpoint. Reload-persistent automatic verification is a
separate follow-up after the next controlled live canary supplies stable DOM
evidence.

## Read Models

Browser bindings and Inbox account controls expose:

- `followback_circuit_state`
- `followback_circuit_reason`
- `followback_cooldown_until_ms`

The existing `browser_followback_enabled` value remains the operator preference.
The UI shows the circuit status beside the follow-back switch and prevents an
enable request while the cooldown is active.

## Testing

Automated tests must prove:

1. Circuits are account-scoped and durable.
2. A cooldown blocks claims without creating a lease.
3. Expiry promotes to canary and permits one action key only.
4. A completed canary closes the circuit.
5. An uncertain canary reopens the 24-hour cooldown.
6. AI and welcome claims are unaffected.
7. Operator commands are idempotent and active cooldown enable returns conflict.
8. Extension uncertain results include the bounded reason.
9. Browser bindings and console read models expose circuit state without secrets.

## Live Acceptance

The two current accounts remain with AI enabled and follow-back disabled during
the platform cooldown. When the operator reports that a manual follow survives
reload, open one account as a canary, create one fresh controlled follower event,
and require all of the following visible evidence:

1. One new follower event.
2. One claimed follow-back lease.
3. A reload-persistent followed/friend state.
4. One completed lease.
5. One welcome plan and one visible welcome message.
6. No duplicate result after page reload.

