# TikPoc Runtime Integrity Hardening Design

## Goal

Close the control-boundary and convergence gaps found by the 2026-07-20 full
repository review before resuming live mobile or browser automation. Preserve
the existing visible-state, account isolation, lease, and idempotency contracts.

## 1. Extension-Only Browser API Origin

Browser action APIs accept only exact origins listed in
`TIKPOC_BROWSER_EXTENSION_ORIGINS`, each matching
`chrome-extension://<extension-id>`. TikTok page origins are never trusted by
default. Missing configuration leaves browser action APIs closed while ordinary
loopback operator APIs remain available.

The production extension already sends localhost requests from its background
service worker. Content scripts communicate through `chrome.runtime.sendMessage`
and never fetch action APIs directly. CORS responses echo only a configured
extension origin. A TikTok page origin cannot read bindings, submit events,
claim or finish actions, report health, or create reply/welcome work.

## 2. Transactional Followback Completion

A verified followback is committed through one server operation containing the
account-bound action identity and normalized follower evidence. In one SQLite
transaction the server verifies the active lease owner, records the terminal
action state, persists the deduplicated `followback_completed` event, and creates
or reuses the welcome plan when account policy permits.

The extension stores local `completed` only after that operation succeeds.
Retries reuse the same action key and event key. A lost HTTP response can be
replayed without duplicating the event, lease transition, or welcome plan.
Uncertain visible results remain uncertain and create no welcome.

## 3. Transactional Monitoring Switch

One account-scoped localhost command updates AI reply and followback automation
switches in a single SQLite transaction and returns the committed pair. The
extension no longer calls two independent endpoints.

For start, the extension verifies localhost, commits the server switch, then
stores the matching local monitoring state and opens or refreshes observer tabs.
For stop, it commits the server switch before storing the stopped local state.
A server failure leaves Chrome storage unchanged. If the local storage commit
fails after the server commit, the extension sends an idempotent compensation
command restoring the previous server state and reports failure.

## 4. Device Fence Revalidation

Every fenced device operation checks the device worker fence immediately before
and immediately after the blocking Appium/ADB call. A lost fence raises
`DeviceWorkerLeaseLost`; the stale worker exits without deferring the assignment.

Action-plan result and assignment terminal writes also validate the current
device/account/owner/fence token and active assignment lease inside their SQLite
transaction. This closes the remaining check-to-write race after a replacement
worker acquires the device. A stale process cannot append action attempts,
change quota state, complete an assignment, or overwrite replacement evidence.

## Verification And Rollout

Each section is implemented and committed separately with test-first coverage,
independent specification review, and independent code-quality review. Run the
full Python, Chrome Node, Android build, Ruff, format, and diff checks before
live recovery. Configure the exact installed extension origin locally, restart
the localhost service, and verify extension requests succeed while TikTok page
origin requests receive `403` before any browser action is enabled.

