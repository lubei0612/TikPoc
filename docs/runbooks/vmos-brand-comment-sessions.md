# VMOS Brand Comment Sessions

## Purpose

Run reviewed first-level TikTok comments from one account per VMOS device while
the APK performs bounded relevant-feed browsing between due comments. The default
hard limit is 20 unresolved or visibly confirmed submissions per account and
Asia/Shanghai local day.

## Prepare Videos And Evidence

1. Use the dedicated desktop Chrome profile to find a relevant bag, styling, or
   luxury video. Copy its canonical `https://www.tiktok.com/@USER/video/VIDEO_ID`
   URL.
2. Add it with `tikpoc comment-video-add --db DB --url URL --command-id ID`.
3. Export comments from `followers` as JSONL. Required fields are `cid`, `text`,
   `digg_count`, `reply_comment_total`, `create_time`, and `language`.
4. Import with `tikpoc comment-evidence-import --db DB --video-id VIDEO_ID
   --file COMMENTS.jsonl --command-id ID`. Replayed `cid` values are ignored.
5. Rank evidence by likes, replies, and recency. Use it to learn tone and
   structure, not to copy a comment verbatim.

## Review And Approve

Create one draft for one named account persona. The English text is the exact
publish text; the Chinese text is the operator translation. The English text is
1–220 characters, contains at most two emoji code points, and contains no URL or
contact destination.

```bash
tikpoc comment-plan-create --db DB --video-id VIDEO_ID \
  --persona-id zoey --account-id ACCOUNT --display-name 'IKUN BAGS | ZOEY' \
  --english 'ENGLISH' --chinese '中文' --emoji-count 1 --command-id ID
tikpoc comment-plan-approve --db DB --plan-id PLAN_ID \
  --account-id ACCOUNT --command-id ID
tikpoc comment-plan-status --db DB --json
```

Approval freezes the account, video, persona, and publish text. One account can
approve only one plan for a video. Status output is redacted and excludes both
comment texts.

## Provision One VMOS Device

1. Bind exactly one TikTok account to the device and register it with the HTTPS
   control plane.
2. Provision the APK with `round_id=brand_comment` and active worker mode.
3. Use ADB only to install/upgrade, verify the package, and enable Accessibility.
   Close the ADB tunnel after heartbeat verification.
4. Do not run the VMOS enhanced-maintenance template and TikPoc at the same time.

The APK pulls tasks over HTTPS, verifies the exact video evidence, checkpoints
`video_verified`, checkpoints `comment_submitting`, submits once, and then checks
for the exact visible text. A lost post-submit response becomes `uncertain`; the
next task is read-only reconciliation rather than a second send.

## Verification Handling And Recovery

When TikTok shows a verification challenge, the APK stops comment gestures,
preserves the queued task, and performs one bounded page reset using exactly two
Android Back navigation actions. It never targets the puzzle widget itself.
After the reset it requires stable Home/Recommended evidence. If the challenge
remains, it records `verification_required` and suppresses new claims for that
device. Other devices continue independently.

1. The two Back actions are automatic and bounded; no repeated reset loop is
   created.
2. If the challenge remains, acknowledge the paused device for a later operator
   review:

   ```bash
   tikpoc comment-recovery-ack --db DB --device-id DEVICE \
     --command-id RECOVERY_ID
   ```

3. Restart the TikPoc Accessibility service. It performs one bounded Home
   recovery, requires visible Home/Recommended evidence, and sends a
   `stable_home` heartbeat.
4. The server resumes that device only after both acknowledgement and the fresh
   stable-Home heartbeat. If the challenge remains, the worker stays paused.

Recovery never clears TikTok data and never adds a follow, direct message,
comment, or like.

## Observe And Roll Back

```bash
tikpoc comment-metrics --db DB --account-id ACCOUNT --json
```

Record planned, submitted, visible-confirmed, uncertain, verification events,
observed likes/replies, profile visits, follows, inbound messages, and qualified
leads at 2 and 24 hours. Keep screenshots, SQLite files, tokens, account details,
and raw comment text only in ignored local storage. Committed fixtures use
synthetic identifiers.

To roll back, pause the device, retain its SQLite task store and server database,
install the previous signed APK, re-enable Accessibility visibly, and resume only
after a stable-Home heartbeat. An unresolved submission continues to consume the
daily quota until reconciliation.
