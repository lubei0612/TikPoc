# Six-Device Daily Capacity Design

## Goal

Raise the current six-device mobile acquisition fleet to more than 10,000 unique
targets per day while preserving the existing business flow. Because every
configured account processes the same target pool, this requires every device to
complete at least 10,000 confirmed target visits per day.

The operational budget assumes 20 productive hours and four hours for account,
network, and device recovery. The minimum sustainable rate is therefore 500
confirmed assignments per device-hour. The promotion target is a fleet mean of
550 or more per device-hour, with the slowest device at 400 or more per hour.

## Current Evidence

The accepted six-device canary measured 452.6 assignments per device-hour, with
individual devices ranging from 362.1 to 553.2. A later stable window reached
roughly 500-560 per device-hour, but did not establish a durable per-device
floor. The current two-device risk-test round has sustained about 700 confirmed
visits per device-hour after rolling interaction quotas became saturated.

The two-device result proves that the adapter and current MYT host can exceed the
daily budget at lower concurrency. It is not direct six-device capacity proof:
the historical measurements show that some UiAutomator hierarchy reads become
two to six times slower when all six slots issue device commands together.
Splitting Appium into six server processes did not improve those tails, so an
Appium process split is not the next optimization.

## Isolated Test Boundary

All capacity work uses a new SQLite database and a new round populated from a
bounded slice of `/Users/chenyuqi/Desktop/tik/all_users_deduped.csv`. The six
current MYT slots process the same logical targets with their existing distinct
order seeds. The production database, production round, and two-device risk-test
database remain paused and unchanged.

The current accounts are test identities only. A passing device/runtime result
may later be reused after the user replaces the accounts, but no account-risk
conclusion transfers automatically to those new identities or proxy exits.

## Business Invariants

- Eligibility remains `following > followers` with at least one visible video.
- A qualifying profile opens one deterministic randomly selected visible video.
- The planned outcome remains like, favorite, repost, or trace before quota
  constraints; rolling limits remain like 100, favorite 14, and repost 25.
- Visible profile identity is required before writing a confirmed visit.
- Video opening and non-trace actions retain visible post-action verification.
- An uncertain action receives one reconciliation and is then held for manual
  retry without a duplicate click.
- Inaccessible profiles retain the bounded terminal skip behavior and do not
  count as confirmed coverage.
- Optimization must not convert an eligible due interaction to trace, weaken
  identity checks, use coordinate fallbacks, or mark attempted navigation as a
  completed visit.

## Measurement And Optimization

First run an unchanged 15-minute six-device baseline. Report wall throughput,
per-device throughput, stage mean/P90/max, action confirmation, uncertain plans,
identity mismatches, process CPU, and Appium/ADB health. Separate quota-saturated
trace throughput from interaction throughput.

If hierarchy-heavy stages show synchronized latency inflation, run one isolated
phase-offset experiment. Each device receives a deterministic startup offset so
that the six workers do not create the same route/identity burst at session
start. The offset changes scheduling only; it does not add per-target sleeps or
change assignment order. Promote it only when fleet wall throughput and the
slowest-device floor improve without increasing errors.

If the baseline instead identifies one or two slow devices independent of fleet
bursts, calibrate those slots separately and retain the shared runtime. Do not
repeat the previously rejected global Appium timeout reduction or independent
Appium-server experiment.

Any further code optimization requires a measured dominant stage, a failing
behavioral test, one implementation variable, and a fresh six-device comparison.

## Acceptance

A candidate is ready for the later new-account run only when a clean 15-minute
window satisfies all of the following:

- fleet mean at least 550 confirmed assignments per device-hour;
- every device at least 400 confirmed assignments per hour;
- projected 20-hour capacity at least 10,000 targets per device;
- zero identity mismatch completions and zero completions lacking confirmed
  visits;
- no duplicate action attempts, quota overruns, or automatic uncertain retries;
- confirmed interactions retain visible evidence and the uncertain rate does not
  regress from the unchanged baseline;
- all six workers, ADB endpoints, Appium sessions, and proxies remain healthy.

If the 550 target is missed but every device remains above 500 for a clean
30-minute window, the build meets the daily capacity requirement but remains
below the preferred operating buffer. A result below 500 is retained only as
diagnostic evidence and is not promoted for the 20-hour daily target.
