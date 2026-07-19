# MYT Six-Device Proxy Guard Design

**Date:** 2026-07-19

## Goal

Keep the six configured MYT Android devices continuously routed through the
existing Clash Verge subscription on the Mac without installing a second proxy
application. The guard operates after the Mac user session is logged in; reboot
before login is outside this scope.

## Existing State

- Clash Verge owns the active remote subscription and Mihomo process.
- The subscription is configured for automatic updates every 1,440 minutes.
- Mihomo exposes an allow-LAN mixed proxy on port `7897`.
- All six devices currently store the Mac endpoint as their Android global HTTP
  proxy and return HTTP `200` for TikTok through that endpoint.
- The Mac receives its LAN address by DHCP, so the address may change while the
  devices continue pointing at the old endpoint.

The subscription URL, provider nodes, selected node, credentials, and response
content remain outside TikPoc configuration, logs, tests, and commits.

## Architecture

Add a small Python proxy guard and a user LaunchAgent.

The guard reads the existing fleet configuration for the MYT host, ADB
endpoints, and expected proxy port. On every cycle it:

1. Resolves the Mac source address used to reach the MYT host.
2. Checks whether the existing Clash mixed proxy accepts a local connection.
3. When Clash is not listening, asks macOS to open the already-installed Clash
   Verge application, waits for a bounded recovery interval, and checks again.
4. Reconnects each configured ADB endpoint.
5. Reads the Android global proxy fields and rewrites them only when they differ
   from the current Mac address and port.
6. Runs a bounded TikTok HTTP probe through the configured proxy when a device
   curl command is available. A missing probe binary is recorded as `unknown`,
   not treated as evidence that the proxy is down.
7. Emits one redacted health row per device containing only device ID, proxy
   state, HTTP status class, and observation time.

The guard never opens TikTok UI, reads account storage, performs engagement
actions, changes the selected provider node, or downloads the subscription.
Clash Verge remains the single owner of subscription updates and proxy routing.

## Recovery Rules

- Local proxy failure is handled before any device changes.
- A device proxy mismatch is corrected idempotently with Android global
  settings, including both the combined and split host/port fields.
- One failed HTTP probe records an unhealthy observation but does not restart
  Clash. Recovery requires repeated local-listener failure or an actual settings
  mismatch, preventing node latency from causing restart loops.
- All subprocesses and network probes have timeouts. One unavailable device does
  not block the other five.
- Logs never include subscription URLs, provider names, usernames, page content,
  request bodies, or command stderr.

## Runtime

Expose `tikpoc proxy-guard` with:

- `--devices config/settings.yaml`
- `--interval 30`
- `--once` for tests and manual verification
- an optional ADB path override

Install a user LaunchAgent with `RunAtLoad` and `KeepAlive` that points to the
active project and writes redacted logs under `~/Library/Logs/TikPoc/`. The
installed service is local machine state; the committed plist is an example and
contains no subscription information.

## Testing And Acceptance

Unit tests use injected address resolution, socket checks, process runners, and
clocks. They cover:

- healthy configuration produces no writes;
- a DHCP address change updates all reachable devices;
- one offline device does not block remaining devices;
- a missing Clash listener triggers one bounded application-open attempt;
- command failures do not expose stderr or subscription-like values;
- repeated cycles are idempotent.

Live acceptance requires one guard cycle to report six reachable ADB endpoints,
six matching Android proxy settings, a listening Clash endpoint, and six TikTok
HTTP `200` results. Then one non-production device proxy is temporarily changed,
the next cycle must restore it, and a final live check must again pass six of six.

