# TikTok Web Engagement Bridge Design

## 1. Goal

Keep the seven mobile trace workers focused on CSV profile visits while a separate
computer-side service handles inbound direct messages and new followers. Direct
messages use TikTok's official Business Messaging Webhook and API. New follower
events use a Chrome extension attached to a dedicated logged-in TikTok profile.

## 2. Scope

Included:

- Verify and ingest TikTok Business Messaging Webhooks without polling TikTok.
- Deduplicate inbound messages by business account and message ID.
- Preserve conversation history and generate an AI reply with a configurable
  private-channel handoff hint.
- Send replies with the official Business Messaging API without touching Android.
- Refresh one-day access tokens from a one-year refresh token.
- Detect new follower entries in TikTok Web activity through DOM mutations.
- Click an inline Follow/Follow back control and report the result to the local
  dashboard API.
- Keep browser and message failures isolated from mobile trace workers.

Excluded:

- Automating TikTok account creation, login, CAPTCHA, or developer-app approval.
- Storing app secrets, access tokens, or refresh tokens in git-tracked files.
- Treating the Codex Chrome plugin as the production runtime. It is a calibration
  tool; the delivered Chrome extension is the long-running runtime.
- Replaying undocumented TikTok web requests outside the logged-in browser.

## 3. Architecture

### Web Event Queue

SQLite gains a `web_events` queue independent from `device_events`. A webhook or
Chrome event is acknowledged after durable insertion. A dedicated worker claims
events transactionally, retries transient failures, and records terminal errors.

### Business Messaging Webhook

`POST /api/tiktok-business/webhook` reads the exact request bytes, verifies the
`TikTok-Signature` HMAC-SHA256 header against the configured app secret, rejects
stale timestamps, parses the JSON `content` field, and accepts inbound message
events only. Supported inbound event names are `im_receive_msg` and
`im_receive_msg_eu`.

The webhook maps `user_openid` to a configured local account, stores the event
using `message_id` as the deduplication key, and returns HTTP 200 without waiting
for the AI or outbound TikTok API.

### Message Worker

Each business account has a nonsecret YAML record containing its local account ID,
TikTok `business_id`, token file path, private-channel handoff hint, and enabled
state. The token file is local, chmod 0600, and contains the app client ID/secret,
access token, refresh token, and expiry timestamps.

For an inbound message the worker:

1. Stores the inbound message in conversation history.
2. Loads a bounded recent history for the conversation.
3. Generates a concise reply in the sender's language.
4. Adds the configured handoff hint only when context makes it useful.
5. Sends a typing action followed by the text reply through
   `/open_api/v1.3/business/message/send/`.
6. Stores the returned message ID and marks the event complete.

TikTok's documented limit of ten outbound messages during each 48-hour window is
treated as an upper bound. The worker also keeps a conservative local counter and
does not retry a send whose result is uncertain.

### Chrome Follower Bridge

A Manifest V3 extension runs only on `https://www.tiktok.com/*`. A content script
uses `MutationObserver` plus a startup reconciliation scan to find activity rows
whose visible text indicates a new follower in English or Chinese. It extracts the
profile link, finds an inline Follow/Follow back button, clicks once, verifies the
button state changed, and posts a sanitized event to
`POST /api/browser-events`.

The extension stores deduplication markers locally so rerenders and browser
restarts do not repeat the click. Each Chrome profile has a small local options
record containing only `account_id`, `device_id`, and the local dashboard URL.

## 4. Configuration

`config/web-accounts.example.yaml` documents the nonsecret account registry.
Token material lives in ignored files referenced by `token_file`.

The dashboard accepts optional environment variables:

- `TIKPOC_TIKTOK_APP_SECRET`: webhook signing secret.
- `TIKPOC_WEB_ACCOUNTS`: account registry path.
- `TIKPOC_WEBHOOK_MAX_AGE_SECONDS`: replay tolerance, default 300.

The message worker uses the same registry and existing LLM environment variables.

## 5. Error Handling

- Invalid or stale webhook signatures return HTTP 401 and are not queued.
- Unknown business accounts return HTTP 202 and record a runtime event without
  exposing payload contents.
- Malformed or unsupported events return HTTP 200 so TikTok does not retry them.
- API 401/403 triggers one token refresh and one retry before failure.
- A response without a confirmed TikTok message ID is treated as uncertain and is
  not automatically resent.
- Browser rows without an unambiguous user link or button are reported as
  `followback_unresolved` and never clicked by coordinates.

## 6. Testing

- Unit tests cover signature parsing, HMAC validation, timestamp tolerance,
  webhook payload parsing, and message API request bodies.
- Database tests cover web-event deduplication, claims, retries, and conversation
  history ordering.
- Dashboard tests cover accepted, duplicate, invalid-signature, and browser event
  requests.
- Message-worker tests use fake AI and API clients.
- Extension parser logic is isolated in a pure JavaScript module and validated
  with Node's built-in test runner against synthetic notification rows.
- A manual Chrome smoke test uses two owned test accounts: one follows and sends a
  message; the target account follows back and replies while the Android worker
  continues processing.

## 7. Acceptance Criteria

- A valid signed inbound DM is durably queued in one HTTP request and duplicates
  do not create a second job.
- The independent worker produces one AI reply and records its confirmed TikTok
  message ID without opening TikTok on Android.
- An access token can be refreshed and atomically persisted without logging it.
- A new follower row in TikTok Web causes one semantic button click and one local
  result event, with no coordinate fallback.
- Mobile task processing continues while web events are received and handled.
- All automated tests and lint checks pass.
