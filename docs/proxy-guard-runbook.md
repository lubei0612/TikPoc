# TikPoc MYT Proxy Guard Runbook

The proxy guard keeps all configured MYT Android devices on the existing Clash
Verge mixed proxy. Clash Verge remains the subscription owner; TikPoc never
stores, downloads, logs, or selects subscription providers.

## Runtime

Run one redacted health cycle:

```bash
uv run tikpoc proxy-guard --devices config/settings.yaml --once
```

The expected healthy summary for the current six-device fleet is:

```text
devices=6 healthy=6 corrected=0 failed=0 http_200=6 http_unknown=0
```

The installed user LaunchAgent runs every 30 seconds after Mac login:

```bash
launchctl print "gui/$(id -u)/com.tikpoc.proxy-guard"
tail -20 ~/Library/Logs/TikPoc/proxy-guard.out.log
```

The committed plist is `launchd/com.tikpoc.proxy-guard.plist`. While feature
work remains in a worktree, the installed copy may point at that worktree; after
branch integration, reinstall the committed primary-checkout version.

## Recovery Behavior

Each cycle verifies the current Mac source address toward the MYT host, Clash
port `7897`, every ADB endpoint, the three Android global proxy fields, and a
TikTok HTTP request. A changed DHCP address or stale device setting is corrected
without opening TikTok. If Clash is closed, the guard opens the existing Clash
Verge application once and performs one bounded listener retry.

One unavailable device is recorded independently and does not block the other
devices. Logs contain a redacted timestamped health row per internal device ID
followed by aggregate counts. They exclude subscription URLs, provider names,
stderr, usernames, response bodies, and account state.

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
