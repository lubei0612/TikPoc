# TikPoc MYT Proxy Guard Runbook

The proxy guard supports both the original Mac Clash listener topology and
device-local Android VPN applications. The VPN application remains the
subscription owner; TikPoc never stores, downloads, logs, or selects providers.

## Runtime

Run one redacted health cycle:

```bash
uv run tikpoc proxy-guard --devices config/settings.yaml --once
```

For device-local VPNs, a healthy row reports `vpn_healthy` or `vpn_recovered`.
The HTTP field remains `unknown` because the MYT ADB shell runs as root and a
root request does not prove that TikTok's application UID used the VPN.

For the original Mac listener topology, the expected healthy summary is:

```text
devices=6 healthy=6 corrected=0 failed=0 http_200=6 http_unknown=0
```

Set an optional `proxy_port` on a legacy device entry when Clash exposes a dedicated
mixed listener for that account. Devices without the field continue to use
`proxy_relay.upstream_port`. The guard checks every configured listener and keeps
each legacy Android global proxy pinned to its assigned port. When Android has
an `always_on_vpn_app`, the guard does not write global proxy settings.

The installed user LaunchAgent runs every 30 seconds after Mac login:

```bash
launchctl print "gui/$(id -u)/com.tikpoc.proxy-guard"
tail -20 ~/Library/Logs/TikPoc/proxy-guard.out.log
```

The committed plist is `launchd/com.tikpoc.proxy-guard.plist`. While feature
work remains in a worktree, the installed copy may point at that worktree; after
branch integration, reinstall the committed primary-checkout version.

## Recovery Behavior

For device-local VPNs, each cycle verifies ADB, the configured always-on package,
its process, `tun0`, and an Android `VPN CONNECTED` network with `VALIDATED` and
the `tun0` interface. If Clash Meta is configured but stopped, the guard sends
its start action once, waits once, and rechecks. Other VPN packages are reported
unavailable instead of being restarted through an unverified intent.

For legacy host listeners, each cycle verifies the Mac source address, listener,
ADB endpoint, the three Android global proxy fields, and a proxied TikTok HTTP
request. A changed DHCP address or stale device setting is corrected without
opening TikTok. If Clash Verge is closed, the guard opens it once and performs
one bounded listener retry.

One unavailable device is recorded independently and does not block the other
devices. Logs contain a redacted timestamped health row per internal device ID
followed by aggregate counts. They exclude subscription URLs, provider names,
stderr, usernames, response bodies, and account state.

## TikTok Readiness

VPN health and TikTok readiness are separate gates. Before starting a fleet,
open one controlled profile on every device and require visible nonzero profile
metrics or a loaded target-profile surface. `Something went wrong`, `Try again
later`, a permanent loading state, or a zero-metric profile fails this gate even
when Android reports a validated VPN. Do not clear TikTok data or count this
probe as a confirmed target visit.

On 2026-07-22 all six devices passed the device-local VPN signals. Slot 3 still
rendered zero metrics and `Something went wrong` after one visible Retry, while
slot 5 rendered a complete target profile. Slot 3 therefore requires a fresh
account-session readiness check before joining the next fleet run.

## Restart And Removal

Restart the guard after changing its code or device configuration:

```bash
launchctl kickstart -k "gui/$(id -u)/com.tikpoc.proxy-guard"
```

Remove the installed service:

```bash
launchctl bootout "gui/$(id -u)/com.tikpoc.proxy-guard"
rm ~/Library/LaunchAgents/com.tikpoc.proxy-guard.plist
```

## Verified Gate

On 2026-07-19, the baseline reported six healthy devices and six TikTok HTTP
`200` results. Slot 6 was then changed to a synthetic stale proxy address. The
LaunchAgent restored it to the current Mac LAN address and port `7897`; the next
cycle again reported six healthy devices and six HTTP `200` results.

On 2026-07-20, six dedicated Clash listeners were assigned to the six MYT
slots. Live checks confirmed six distinct egress addresses, six TikTok HTTP
`200` responses, and a final guard cycle with `healthy=6`.
