# Web Event Bridge Runbook

## 1. Process layout

The mobile workers continue consuming CSV trace tasks. They do not open Inbox for
the web flow.

- `tikpoc dashboard`: receives signed TikTok DM webhooks and Chrome follower events.
- The Dashboard background worker consumes only `web_events`, calls the AI
  service, and sends replies with TikTok Business Messaging API.
- `chrome-event-bridge`: observes the dedicated TikTok Web activity panel and
  follows back through a semantic button click.

The three paths share SQLite for durable deduplication but use separate queues.

## 2. Local account registry

Edit `config/web-accounts.yaml` for every TikTok/Chrome/device binding:

```yaml
accounts:
  - account_id: account-01
    device_id: phone-01
    business_id: the-business-open-id-from-tiktok-oauth
    token_file: secrets/account-01-token.json
    private_channel_hint: "Continue on WhatsApp: your-contact"
    enabled: true
```

Copy the field structure from `config/business-token.example.json` into the
ignored token path. Expiry values are Unix timestamps in seconds. The worker
refreshes the one-day access token and writes the replacement file atomically
with mode `0600`.

Do not put a placeholder contact in `private_channel_hint`. Leave it empty until
the real handoff channel is ready.

## 3. Environment

Both commands load `.env.local` by default without overriding variables already
set by the service manager. The configured AI variables are:

```text
TKAUTO_LLM_BASE_URL=https://ai98pro.xyz/v1
TKAUTO_LLM_MODEL=gpt-5.4-mini
TKAUTO_LLM_API_KEY=<local secret>
```

Add these values after the TikTok developer app is approved:

```text
TIKPOC_TIKTOK_APP_SECRET=<webhook signing secret>
TIKPOC_WEB_ACCOUNTS=config/web-accounts.yaml
TIKPOC_WEBHOOK_MAX_AGE_SECONDS=300
```

## 4. Start services

Use the private runtime directory under `/Users/Shared`. On this Mac, background
LaunchAgents can read project code from Desktop but cannot reliably create SQLite
WAL write locks in Desktop or the user's protected Library directories. The
installed directory is owned by the current user with mode `0700`.

```bash
DB="/Users/Shared/TikPoc/tikpoc.db"

uv run tikpoc dashboard \
  --db "$DB" \
  --host 127.0.0.1 \
  --port 8766 \
  --web-accounts config/web-accounts.yaml \
  --with-web-worker \
  --web-worker-idle-sleep 0.5
```

The installed macOS LaunchAgent is sourced from `launchd/` and runs the HTTP
server plus the asynchronous queue consumer in one process. This avoids
cross-process SQLite WAL restrictions observed on this Mac. Logs are under
`~/Library/Logs/TikPoc/`. The standalone `tikpoc web-worker` command remains
available for container or server deployments with a normal shared SQLite
filesystem.

Expose only `/api/tiktok-business/webhook` through the HTTPS reverse proxy used
for the TikTok developer app. Keep `/api/browser-events`, `/api/status`, and the
dashboard bound to localhost.

## 5. TikTok Business Messaging setup

1. Authorize each eligible TikTok Business Account through the developer app.
2. Request Business Messaging API access and complete the required app review.
3. Store the returned business open ID and OAuth tokens in the local files.
4. Register the public HTTPS webhook URL and subscribe to `DIRECT_MESSAGE`.
5. Send one DM from a second owned account and confirm one `dm_received` event,
   one outbound reply, and one recorded TikTok outbound message ID.

The worker enforces the documented maximum of ten outbound messages in the
48-hour window opened by an inbound message. It never automatically retries an
outbound request whose result is uncertain.

## 6. Chrome extension setup

1. Open `chrome://extensions` in a dedicated Chrome Profile.
2. Enable Developer mode and load this directory as unpacked:
   `/Users/chenyuqi/Desktop/tik/chrome-event-bridge`.
3. Open the extension settings and enter the exact `account_id`, `device_id`, and
   `http://127.0.0.1:8766`.
4. Use **Test connection** before enabling automatic followback.
5. Keep one dedicated TikTok tab signed into that account. Enable automatic
   activity-panel opening only in this dedicated tab/profile.

The content script requires all three signals before clicking: explicit follower
notification text, a valid `tiktok.com/@username` link, and an exact Follow or
Follow back button label. Ambiguous rows are recorded as unresolved and are not
clicked by coordinates.

## 7. Acceptance check

Use two accounts you control.

1. Start one mobile trace worker and note its active target.
2. From the second account, follow the target TikTok account and send a DM.
3. Confirm the Chrome bridge follows back once.
4. Confirm the DM gets one AI reply without the Android device opening Inbox.
5. Confirm the mobile trace worker advances independently.
6. Repeat the same webhook and browser payload and confirm both return
   `accepted: false` because the deduplication keys already exist.

The real-account check cannot be completed until the TikTok Business Messaging
permission, business open ID, OAuth token file, and public HTTPS webhook are
available.
