# Trusted Browser Message Input Design

**Date:** 2026-07-20

## Goal

Allow every configured TikTok Chrome Profile to keep observing followers and messages and to submit AI replies through Chrome-trusted keyboard input, so customer service continues while the operator is offline or managing many accounts.

## Decision

Use the Manifest V3 `chrome.debugger` API only for the final message-input boundary. The existing content scripts continue to identify the visible account, observe Activity and Messages, establish historical baselines, request durable plans, and claim account-scoped action leases. After a lease is claimed and the target conversation is revalidated, the Messages content script asks its own background service worker to perform a trusted send in the sender tab.

The background worker validates that the request came from a TikTok Messages route, serializes sends per tab, attaches the debugger, clears the focused composer with trusted key events, inserts the planned text with `Input.insertText`, presses Enter, and detaches in a `finally` block. It returns only whether the input was submitted; the content script still requires the exact outbound bubble before recording `sent` and `completed`.

## Multi-Account Boundary

- Account count remains configuration-driven through `config/web-accounts.yaml`.
- Each Chrome Profile has its own extension service worker and TikTok login.
- Python remains the owner of plans, reply budgets, suppression, funnel state, and account-scoped leases.
- The trusted-input request contains no account credentials, cookies, model keys, or stored contact destinations.
- Concurrent sends in one tab are serialized; sends in separate Profiles remain independent.

## Trusted Send Contract

The content script sends:

```json
{
  "type": "TIKPOC_TRUSTED_SEND",
  "text": "Thanks for your interest. Please check our TikTok profile link."
}
```

The background worker accepts the request only when:

1. `sender.tab.id` is present;
2. `sender.url` is `https://www.tiktok.com/messages*` or `https://www.tiktok.com/business-suite/messages*`;
3. the normalized text is nonempty and no longer than TikTok's current `6000` character composer limit;
4. no other trusted send is active in the same tab.

The result is `{submitted: true}` after debugger commands complete. Attachment failure, invalid origin, command failure, or detach failure returns an error. The content script records `uncertain` and keeps the durable lease busy whenever exact visible reconciliation does not pass.

## Input Sequence

1. Revalidate the active conversation and inbound fingerprint.
2. Focus the unique visible composer in the content script.
3. Attach `chrome.debugger` to the sender tab.
4. Send trusted `Meta+A`, `Backspace`, `Input.insertText`, and `Enter` commands.
5. Detach immediately in `finally`.
6. Poll the visible conversation for the exact normalized outbound text.
7. Record `sent/completed` only for an exact bubble; otherwise record `uncertain`.

No synthetic `textContent`, `input`, `change`, or direct send-button click remains in the production send path.

## Failure Handling

- A tab already controlled by another debugger produces an explicit trusted-input error and no fallback click.
- A route or target change before claim supersedes the plan; a change after submission remains `uncertain` until visible reconciliation.
- Service-worker suspension is safe because the Python plan and action lease are durable and duplicate claims remain blocked.
- The debugger detaches after both success and failure. No persistent debugging session or message body log is retained.
- Automatic follow-back stays on its existing visible-button path and is unaffected.

## Operator Experience

Loading or reloading the unpacked extension grants the additional Debugger permission. Chrome may display a debugging notification only during the short send interval. Daily operation remains: keep the Profile and TikTok pages open, tell AI or CLI to connect the Chrome, and monitor account-scoped health and switches from the local console.

### One-click monitoring

The popup adds a primary `开始监控` / `停止监控` control. Start first verifies the loopback service, stores a Profile-local `monitoringStarted=true` state, enables the bound account's AI reply and follow-back server switches, and creates or reuses exactly one TikTok Messages tab plus one non-Messages TikTok observer tab. If the account has not been bound yet, opening the observer tab lets the existing visible-username auto-connector bind it; the background worker enables server switches as soon as the account mapping appears in extension storage.

While monitoring is started, browser startup, the existing health alarm, and monitored-tab removal all run the same idempotent tab check. Missing pages are reopened, existing pages are reused, and unrelated TikTok tabs are left untouched. Stop stores `monitoringStarted=false`, disables the bound account's server action switches, and leaves tabs and binding data in place.

The extension does not launch arbitrary local executables. The existing TikPoc launchd service remains the login-started companion process; a failed loopback health check leaves monitoring stopped and shows the service error in the popup. Each Chrome Profile has independent popup state, tabs, binding, and server account mapping.

## Acceptance

Automated tests must prove route validation, per-tab serialization, command order, detach-on-error, no synthetic fallback, exact visible reconciliation, one-click idempotent tab creation, persisted restart recovery, account switch enable/disable, and multi-account isolation. Live acceptance uses the popup in both controlled Profiles to create/reuse observer pages and establish `4/4`, sends one fresh inbound in each direction, verifies one AI reply and one durable `sent/completed` result, reloads both sides to prove no duplicate, and keeps any TikTok rejection as `uncertain`. A fresh controlled follow remains required to close the visible automatic-follow-back and welcome gate.
