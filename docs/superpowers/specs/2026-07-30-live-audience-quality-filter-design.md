# Live Audience Quality Filter Design

**Date:** 2026-07-30  
**Status:** Approved for planning

## Goal

Submit only higher-intent TikTok LIVE audience members to TikPoc's
`live_interrupt` profile-touch lane while retaining all collected public event
evidence for later analysis.

## Scope

This change covers audience classification in the existing `followers`
collector, the JSONL handoff contract, and defensive validation at TikPoc's live
batch boundary. It does not change APK navigation, profile identity checks,
interaction rules, live-batch priority, barrier behavior, or comment-session
scheduling.

## Classification

Each deduplicated audience identity receives one deterministic level based on
its accumulated public LIVE events:

- **A:** at least one `comment`, `follow`, `share`, or `gift` event.
- **B:** no A-level event, but at least one `like` event, passive activity in
  more than one room, or at least two distinct supported event types.
- **C:** only passive `join` evidence from one room.

The strongest applicable level wins. Repeated deliveries of the same event do
not weaken or duplicate an identity. Missing usernames remain in the raw store
but are ineligible for handoff because the mobile identity check requires an
exact username.

## Data Flow

1. `live_audience_collector.py` continues recording every supported event in
   SQLite without filtering.
2. Export derives `lead_level` and `qualification_reasons` from each accumulated
   row.
3. The analytical CSV contains A, B, and C rows with their derived quality
   fields.
4. The TikPoc JSONL exporter writes only A/B identities with valid usernames.
5. Each JSONL record includes `lead_level` and
   `qualification_reasons`, alongside the existing stable identity and source
   fields.
6. TikPoc validates that submitted quality metadata is A or B. Legacy live files
   without quality metadata remain accepted so existing integrations do not
   break; collector-produced files always include it.
7. Existing stable-ID and normalized-username deduplication runs before the
   batch enters `live_interrupt` scheduling.

## Interface Contract

Example collector output:

```json
{"username":"buyer.one","user_id":"123456","sec_uid":"MS4w...","profile_url":"https://www.tiktok.com/@buyer.one","source_type":"live_audience","source_id":"live-20260730-01","source_live_id":"luxury-room","collected_at":"2026-07-30T20:00:00+08:00","navigation_mode":"deeplink","lead_level":"A","qualification_reasons":["comment"]}
```

`qualification_reasons` is a sorted, nonempty JSON array. Allowed reasons are
`comment`, `follow`, `gift`, `share`, `like`, `multiple_event_types`, and
`multiple_rooms`.

## Error Handling

- The collector writes to a temporary file and atomically renames it only after
  all eligible rows are serialized.
- Unknown collector event names do not qualify an identity; known qualifying
  evidence still determines its level.
- TikPoc rejects explicit `lead_level` values other than A or B and rejects
  malformed `qualification_reasons` without partially creating a batch.
- An export containing zero A/B identities completes as a valid empty export;
  submission reports that there are no eligible targets instead of scheduling a
  device batch.

## Observability

Collector completion reports raw unique identities plus A, B, and C counts and
the number handed to TikPoc. Batch status retains the source identifier and
normal target/device completion accounting. No credentials, cookies, proxies,
or private browser state enter exported files or logs.

## Testing

- Unit-test classification precedence and the A/B/C boundaries.
- Unit-test that raw CSV retains C while TikPoc JSONL excludes C and missing
  usernames.
- Unit-test deterministic reasons and atomic export behavior.
- Unit-test TikPoc acceptance of A/B and legacy records, plus rejection of
  explicit C or malformed quality metadata.
- Run the followers test suite, focused TikPoc importer/API tests, full Python
  regression, Ruff on touched files, and `git diff --check`.

## Acceptance

- No join-only identity reaches a newly generated live-interrupt batch.
- Every collector-generated submitted identity is classified A or B with a
  reproducible reason.
- Existing live-interrupt navigation and interaction behavior remains unchanged.
- Raw evidence remains available for later threshold analysis.
