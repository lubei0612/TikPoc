# Chrome TikTok Account Binding Handoff

## Goal

Bind the TikTok account already signed in to Chrome to the local TikPoc web event
bridge without logging out, reading cookies, or changing unrelated Chrome tabs.

## Local runtime

- Project: `/Users/chenyuqi/Desktop/tik`
- Dashboard: `http://127.0.0.1:8766`
- Extension directory: `/Users/chenyuqi/Desktop/tik/chrome-event-bridge`
- Account ID: `account-01`
- Device ID: `phone-01`
- Account registry: `/Users/chenyuqi/Desktop/tik/config/web-accounts.yaml`

## Browser procedure

1. Stop the previous expense-filing task and list the current Chrome tabs.
2. Select the already signed-in `tiktok.com` tab. Do not inspect cookies, local
   storage, passwords, or Chrome profile files.
3. Read only the public TikTok username and public profile URL visible in the UI.
4. Open the Google Chrome Extension Manager and load the unpacked extension from
   `/Users/chenyuqi/Desktop/tik/chrome-event-bridge` if it is not already loaded.
   If Chrome blocks automation on an internal page, ask the user only to perform
   the Load unpacked click, then continue automatically.
5. Open the TikPoc Event Bridge options and set:
   - Account ID: `account-01`
   - Device ID: `phone-01`
   - Dashboard URL: `http://127.0.0.1:8766`
6. Run **Test connection**. Do not enable automatic followback unless the test
   succeeds.
7. Enable automatic followback and automatic activity-panel opening. The first
   visible activity list is a baseline and must not trigger clicks on historical
   notifications.
8. Keep one dedicated TikTok tab open. Confirm the bridge popup reports Running,
   account `account-01`, and device `phone-01`.
9. Report the public TikTok username and whether the connection test passed. Do
   not expose session material or API keys in the response.

## Business Messaging

After the browser bridge is bound, open the official TikTok developer/business
portal and determine whether the signed-in account already has:

- an approved developer app,
- Business Messaging API permission,
- a business open ID,
- OAuth access and refresh tokens,
- an app secret for Webhook signature verification.

Do not invent or extract these values from the normal TikTok web session. If an
application or approval is missing, report the exact missing prerequisite and
stop before submitting any application or accepting legal terms.

## Acceptance

- `http://127.0.0.1:8766/api/status` responds successfully.
- Extension settings match `account-01` and `phone-01`.
- Historical activity notifications are baselined, not followed in bulk.
- A new owned-account follower notification produces one deduplicated browser
  event and one semantic Follow/Follow back click.
