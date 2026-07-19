# TikPoc Mobile Fleet Runbook

## MYT Slot 1 Functional Gate (2026-07-19)

The first complete live functional round used MYT slot 1 with TikTok `44.8.42`
and the current 372-row comment export. Import produced 326 stable targets and 46
duplicate source rows. The round finished with:

- 326/326 completed assignments and confirmed visits;
- 326 immutable qualification snapshots;
- 119 eligible profiles and 37 private profiles;
- 21 confirmed favorites, 37 likes, 37 reposts, and 231 trace outcomes;
- zero duplicate visits, nonterminal plans, uncertain quota reservations, or
  active worker leases at completion.

Use an ignored local configuration and database:

```bash
uv run tikpoc pool-import --db var/tikpoc.db --csv PATH_TO_CSV
uv run tikpoc round-create \
  --db var/tikpoc.db --pool POOL_ID \
  --devices config/settings.yaml --starts-at ISO_8601_WITH_TIMEZONE
uv run tikpoc fleet-run \
  --db var/tikpoc.db --round ROUND_ID --devices config/settings.yaml
```

TikTok 44.8.42 uses the current profile IDs `rgn`, `rfd`, `rfc`, `efq`, and
`z9h`. Fleet Appium sessions must set `waitForIdleTimeout=0`. Favorites expose
no reliable selected/checked accessibility state; verify the bounded favorite
button screenshot contains the active yellow icon. Stable `user_id` profile
routes handle renamed accounts; require a visible profile transition, using the
Inbox baseline when the device is already on the same renamed profile.

## Capacity Result

This functional round is not a capacity pass. Debugging and repeated recovery
were retained in the durable history. The measured mean was 170.718 seconds,
P90 was 15.572 seconds, and projected 20-hour capacity was 421 targets/day.
The capacity command correctly failed on timing and historical identity mismatch
evidence. Run a fresh, calibration-free round before evaluating promotion.

## Paced Slot 1 Final-Attempt Gate (2026-07-19)

A new database and round reused the available 326-unique-target CSV after the
rolling pacing and route-recovery changes. Durable business state finished with:

- 326/326 completed assignments, confirmed visits, and complete coverage;
- 45 confirmed likes, 7 favorites, 11 reposts, and 263 trace-only visits;
- zero uncertain plans and zero active assignment leases;
- no quota integrity, false-completion, identity, or cardinality findings.

The actions were created across approximately 26 minutes. Their relative counts
track the configured hourly rates of 100/14/25 rather than firing at round start.

Capacity uses the final `profile_opening` attempt for a recovered assignment and
accepts `pacing_not_due` only for a quota-free trace plan. The resulting audit was:

- mean 4.658 seconds, P90 6.980 seconds;
- 772.8 confirmed targets/hour;
- projected 15,455 unique targets per 20-hour day;
- route/identity/metrics/video/action P90 values of 1.302/2.461/0.996/2.022/2.461 seconds.

One target exposed a stable-user-ID route that remained blank for more than 60
seconds. The bounded recovery now tries the stable route, Inbox baseline, one app
restart, then the public username URL with exact visible-username verification.
That target completed through the final fallback.

This is measured 326-target final-attempt evidence, not the planned clean
500-unique-target promotion gate. The run was interrupted for route diagnosis and
the affected assignment accumulated earlier failed attempts before the fix. Keep
the 500-target gate open until a larger unique input is available and run it from
an unchanged build.
