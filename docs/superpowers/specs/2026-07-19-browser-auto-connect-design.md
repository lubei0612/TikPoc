# Browser Auto-Connect Design

## Goal

After an operator loads the unpacked TikPoc extension once in a dedicated Chrome
Profile, the extension must identify the visible TikTok login, bind the unique
matching configured account, and report Activity and Messages health without
requiring Account ID or Device ID entry. Operators can use the same flow through
the extension UI, an AI browser session, or a local CLI health command.

## Product Boundary

- Chrome still requires the operator to load or update the unpacked extension.
- TikPoc never reads Chrome profile storage, cookies, tokens, or credential files.
- Account detection uses only visible TikTok DOM identity.
- The Python registry remains the source of account ID, device ID, expected
  TikTok username, Profile label, and feature enablement.
- Automatic actions remain blocked until visible identity and server mapping are
  both ready.

## Auto-Binding

On a TikTok page, the content script evaluates the visible account identity. If
exactly one normalized username is visible and it matches exactly one enabled,
binding-ready server account, the extension stores the server-provided mapping
atomically in `tikpocSettings`.

Automatic binding uses these rules:

1. Fetch the redacted binding list from the configured loopback Dashboard URL.
2. Match the normalized visible username case-insensitively to
   `expected_tiktok_username`.
3. Accept only one enabled and binding-ready match.
4. Preserve runtime choices such as automatic Activity opening and feature
   switches while replacing account, device, expected username, and Profile
   label as one unit.
5. When the account changes, clear only the old account's follower and DM
   baselines and processed records, using the existing scoped reset behavior.
6. Establish fresh baselines before any follow-back or reply workflow runs.

Signed-out, verification, ambiguous, missing, and mismatch states remain
unbound. They update visible status but never select a nearest or historical
account.

## Manual Operation

The settings page defaults to automatic identification and displays the
observed username, matched account, server connection, and Activity/Messages
health. The existing server-provided account menu remains available as an
explicit manual override. Rebinding through the menu retains its confirmation
and scoped reset behavior.

The popup presents a compact state summary and does not expose editable account
or device identifiers.

## CLI Operation

Add a `tikpoc browser` command group with local-only operations:

- `tikpoc browser connect`: validate the account registry, verify the loopback
  Dashboard binding endpoint, and wait for configured account/page-role health.
- `tikpoc browser status`: print redacted account, Profile label, expected and
  observed usernames, page role, binding state, and heartbeat age.
- `tikpoc browser guide`: print the unpacked extension path and concise manual
  installation and recovery steps.

The CLI does not install extensions, manipulate Chrome internals, edit browser
profile data, or bypass login and verification screens. It returns a nonzero
status when the server is unavailable, mappings are invalid, or required health
rows are not ready before the timeout.

## AI-Assisted Operation

When the operator says "connect this Chrome", an AI agent follows the same
contract:

1. Confirm the selected Chrome page exposes one visible TikTok username.
2. Start or verify the loopback service with the configured registry.
3. Reload the TikTok page after extension or service changes.
4. Run `tikpoc browser connect` and inspect redacted health.
5. Report the exact blocked identity state when readiness is not reached.
6. Enable follow-back or AI replies only after the corresponding page-role
   health is ready and the operator has approved real actions.

## Data Flow

The content script sends the observed username to the extension background. The
background requests `/api/browser-bindings`, resolves no identity itself, and
returns the redacted list. A pure auto-binding helper selects zero or one match
and produces the next atomic settings value. Activity and Messages scripts then
report account-scoped health. Python validates observed username and binding
state again before persisting health, plans, leases, or results.

## Failure Handling

- Dashboard unavailable: retain the previous mapping, show disconnected, and
  perform no new action.
- No visible identity: show signed out or unverified and perform no action.
- Verification page: show verification required and perform no action.
- No server match: remain unbound and direct the operator to update the local
  registry.
- Multiple matches: remain unbound and report ambiguous configuration.
- Previously bound username changes: pause workflows, clear only the previous
  account's scoped extension state after a unique replacement is accepted, and
  establish new baselines.
- Lost action result: preserve the existing uncertain lease and reconciliation
  behavior.

## Verification

Automated coverage must prove unique match, case normalization, no match,
ambiguous match, signed-out and verification states, atomic settings updates,
account-scoped reset, disabled-feature health, CLI success and timeout, and
redacted status output.

Live acceptance uses two dedicated Profiles and verifies:

- automatic mapping for `account-01` and `account-02`;
- Activity and Messages health ready for both accounts;
- one follow-back in each direction with visible completed state;
- three DMs in each direction with one reply per inbound fingerprint;
- reload and rerender deduplication;
- invitation cooldown when a private destination is configured;
- contact capture and human handoff;
- independent mobile worker progress;
- no cookies, tokens, message bodies, contacts, or private destinations in
  committed evidence.
