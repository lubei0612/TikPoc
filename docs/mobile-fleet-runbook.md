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

