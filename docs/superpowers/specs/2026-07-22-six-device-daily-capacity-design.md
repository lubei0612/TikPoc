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

## Twelve-Device Scaling Boundary

Device count is not treated as free concurrency. The current two-device and
six-device measurements show that per-device latency rises when more MYT slots
on the same host read UI hierarchies at the same time. Each ADB endpoint and
UiAutomator2 system port is already distinct, and a previous multi-Appium-server
experiment did not remove the tails. The shared MYT host is therefore the
capacity boundary until a concurrency curve proves otherwise.

Twelve devices must be deployed as explicit execution shards. Each shard owns a
bounded set of device/account pairs, its ADB/Appium processes, proxy health, and
worker supervision. The durable round remains central so every configured
account still processes the same logical target pool and coverage remains
account-specific. A shard failure must stop only that shard and preserve its
assignment checkpoints.

Before twelve-device promotion, measure identical 15-minute windows at two,
four, and six devices on one MYT host. Choose the largest shard size whose
slowest device remains at or above 500 confirmed assignments per hour in a clean
30-minute window. Deploy twelve devices as `6+6`, `4+4+4`, or smaller shards on
independent MYT execution hosts according to that result. Running twelve slots
on one host is not accepted from configuration symmetry or short synthetic
tests.

The twelve-device gate requires every shard and every device to satisfy the same
identity, visit, action, quota, uncertain, and proxy checks as the six-device
gate. Report both per-shard and fleet totals so a fast shard cannot hide a slow
or unhealthy one.

## Current July 22 Evidence

The unchanged six-device window produced 306 completions in 758.4 seconds. Slot
3 had no completions because its device-local Clash service was not running.
Excluding that failed slot, the mean was 290.5 assignments per device-hour.

The deterministic startup-offset comparison produced 427 healthy-slot
completions in 912.4 seconds. Excluding slot 3, the mean improved to 336.9 per
device-hour and the slowest healthy device improved to 299.9 per hour. Identity
and confirmed-visit integrity checks remained clean. This is a measured
improvement, but it is below the daily-capacity promotion gate.

Slot 3 now has the Clash foreground/background processes, a `tun0` interface,
and an Android-validated VPN network. Its TikTok profile still renders zero
metrics and `Something went wrong`, while slot 5 renders a complete target
profile at the same time. Slot 3 therefore remains excluded from business
acceptance until a fresh account session passes a visible profile probe; VPN
presence alone is not sufficient evidence.

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
