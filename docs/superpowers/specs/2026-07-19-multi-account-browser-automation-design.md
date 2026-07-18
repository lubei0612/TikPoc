# Multi-Account Browser Automation Design

## Goal

Run follow-back and inbound-message conversion for any number of configured
TikTok Web accounts. Two Chrome Profiles are the first live acceptance fixture;
the production model is one dedicated Chrome Profile per configured account,
with no fixed account count in code or storage.

## Existing Foundation

The localhost service already scopes reply plans, action leases, funnel state,
and health by `account_id`. Each Chrome Profile has independent extension local
storage, so the current bridge can be installed repeatedly without sharing
cookies or TikTok session data. Follow-back and DM sends already require visible
DOM controls and server leases.

The remaining risk is configuration identity, not worker count. Today an
operator types `accountId` and `deviceId` into each extension profile, the web
page login is not compared with an expected TikTok username, and the registry
does not reject two accounts mapped to the same device.

## Configuration And Binding

Each browser account adds two nonsecret fields:

```yaml
accounts:
  - account_id: account-01
    device_id: myt-slot-01
    mode: browser
    expected_tiktok_username: shop_account_one
    browser_profile_label: TikPoc 01
    browser_followback_enabled: true
    browser_dm_enabled: true
    enabled: true
```

`account_id`, `device_id`, and `expected_tiktok_username` are unique after
case-normalization. `browser_profile_label` is an operator label and need not
match a filesystem directory. Existing browser accounts without an expected
username load in `binding_unverified` state: health is reported, but follow-back
and DM actions stay disabled until the field is configured.

The localhost API exposes an account-binding list containing only account ID,
device ID, expected username, profile label, feature switches, and readiness.
It never returns private-channel destinations, offer/FAQ content, message text,
cookies, tokens, or Chrome profile paths. The extension options page loads this
list after a successful localhost connection and uses a Chinese account menu
instead of independent free-text account/device inputs.

## Visible Identity Gate

Both Activity and Messages adapters derive the signed-in TikTok username from a
single visible profile link or account control. Ambiguous or absent identity is
`unverified`; a different username is `mismatch`. Before observation can claim
an action lease, the content script requires:

1. master and feature switches enabled;
2. account/device mapping returned by the localhost service;
3. exactly one visible signed-in username;
4. normalized visible username equal to `expected_tiktok_username`.

Health payloads include the normalized observed username and one of `ready`,
`unverified`, `mismatch`, `signed_out`, or `verification_required`. The service
validates account/device/expected-username consistency before accepting a plan,
lease, result, or healthy heartbeat. A mismatch blocks only that browser
account and never pauses mobile workers or other Chrome Profiles.

## N-Account Execution

No central browser loop iterates a fixed account list. Every Chrome Profile runs
the same extension instance against its own local settings; the Python service
accepts concurrent requests for all enabled registry entries. Server state
remains account-scoped:

- follower dedup keys include account and visible follower identity;
- inbound fingerprints include account and conversation identity;
- reply plans remain unique by account and inbound fingerprint;
- action leases include account, device, action type, and action key;
- uncertain results remain busy until reconciliation or lease expiry;
- account enable switches can stop follow-back or DM independently.

The management console lists every configured account and both page-role health
rows. Chinese status text distinguishes 未绑定、身份不符、已退出、需验证、已就绪,
and heartbeat expiry. It provides per-account feature switches through existing
idempotent operator commands; there is no fleet-wide browser navigation.

## Baseline And Duplicate Prevention

Installing or rebinding a Chrome Profile establishes new Activity and Messages
baselines without acting on historical rows. Rebinding clears only extension
baseline keys for the previous account after explicit confirmation. Page reload,
DOM rerender, duplicate tabs, and repeated server responses reuse the same
fingerprint and plan. A visible send that cannot be reconciled becomes
`uncertain`; it is not sent again immediately.

## Automated Acceptance

Python tests cover arbitrary registry counts, unique device/username mappings,
redacted binding API output, two-account plan isolation, and identity-gated
health/actions. Node tests cover visible username extraction, ambiguous and
mismatched identity, account menu persistence, account-scoped baseline reset,
and two concurrent profile workflows with equal conversation/message IDs.

All Python, extension Node, frontend, and Playwright suites remain green.

## Two-Account Live Acceptance

The user is notified before live testing. Two dedicated Chrome Profiles are
opened and the user signs in only where a session is missing. For each direction
between the two controlled accounts:

1. verify the visible username and both Activity/Messages health rows;
2. create one new follow and confirm exactly one visible follow-back;
3. send three inbound messages and confirm one reply per fingerprint;
4. verify the configured private-channel invitation policy and funnel stage;
5. reload and rerender the active thread and confirm no duplicate send;
6. create a contact/human-handoff signal and confirm AI stops ordinary replies;
7. verify the mobile worker continues independently.

An account is marked ready only after both directions pass without cross-account
plans, stale leases, duplicate sends, or identity warnings.
