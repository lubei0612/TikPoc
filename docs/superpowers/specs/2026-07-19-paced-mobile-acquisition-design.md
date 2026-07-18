# Paced Mobile Acquisition Design

## Goal

Use every account's rolling-hour interaction budget as evenly as available
eligible traffic permits, retain randomized trace visits, accept profiles with
at least one post, and keep TikTok profile navigation below the production
latency gate without weakening visible identity or action verification.

## Eligibility

A public profile is eligible when `following > followers` and `posts >= 1`.
Private, unavailable, zero-post, or incomplete profiles remain trace-only.
Every confirmed profile visit is durable independently from the interaction
result.

## Rolling Quotas And Pacing

Per account limits remain like 100, favorite 14, and repost 25 in every rolling
3,600,000 ms interval. Planned, executing, uncertain, and confirmed actions all
reserve capacity until their timestamp leaves the window. A transaction must
never create a plan that makes rolling usage exceed its limit.

Each device/outcome owns a durable token bucket with capacity two. Tokens refill
at `limit / 3,600,000` per millisecond. Initial token fractions are derived from
the device/outcome seed so the three actions do not start together. At an
eligible target, all actions with a full token and rolling headroom become
candidates. A deterministic seeded weighted draw selects among candidates; the
selected token is consumed in the same transaction as plan creation. The second
token preserves small overdue fractions caused by discrete target arrivals but
never overrides the exact rolling limit. When no
action is due, the outcome is trace. This naturally intersperses trace visits
and distributes maximum usage over the full hour.

Existing plans are immutable. Retries reuse their original requested and
effective outcomes. Uncertain work never receives another click.

## Navigation

Use the native stable `user_id` route first. The route is successful only after
the visible profile marker is nonempty and the metric/post surface is ready.
ADB `am start -W` supplies command and activity timing; Appium verifies visible
state. If the route remains on the previous profile or For You, navigate to the
Inbox baseline and retry once. If readiness still fails, restart TikTok and
retry once, then defer with a specific stage error.

Read username, counts, and post containers through targeted resource IDs before
falling back to bounded XML parsing. Record route-command, identity-ready,
metrics-ready, video-ready, and action-ready timings. Two consecutive slow or
stale routes trigger recovery; ordinary fast routes do not incur fixed sleeps.

## Console Contract

Expose rolling usage, limit, reserved/uncertain count, token readiness, next due
time, and the current candidate weight for each account/outcome. Rename the
fixed-hour quota surface to rolling one-hour quota.

## Acceptance

- Rolling usage never exceeds 100/14/25, including uncertain reservations.
- A high-volume synthetic hour paces actions across the hour instead of
  front-loading them.
- Profiles with one post are eligible; zero-post profiles are trace-only.
- Visible route identity and metrics are required before visit/snapshot success.
- A clean 500-target device run has mean below 6.5 s and P90 below 8.64 s.
