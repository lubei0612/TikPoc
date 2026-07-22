# Six-Device Daily Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify and raise six-device throughput to a sustainable 10,000-target daily capacity without changing acquisition behavior.

**Architecture:** Establish an isolated six-device round, measure the unchanged build, then test deterministic worker startup offsets only when the baseline shows synchronized hierarchy contention. Keep production state untouched and promote changes only through measured wall-clock and integrity gates.

**Tech Stack:** Python 3.12, Appium/UiAutomator2, ADB, SQLite, pytest, Ruff.

---

### Task 1: Isolated Six-Device Baseline

**Files:**
- Create runtime artifact: `/Users/Shared/TikPoc/tikpoc-six-capacity.db`
- Create runtime artifact: `/Users/Shared/TikPoc/config/settings.six-capacity.yaml`
- Create runtime artifact: `/Users/Shared/TikPoc/six-capacity-monitor.ndjson`
- Read: `config/settings.yaml`
- Read: `/Users/chenyuqi/Desktop/tik/all_users_deduped.csv`

- [ ] **Step 1: Re-establish the automated baseline**

Run:

```bash
uv run pytest tests/test_appium_device.py tests/test_mobile_worker.py tests/test_fleet_runtime.py -q
uv tool run ruff check src/tikpoc/device.py src/tikpoc/mobile_worker.py src/tikpoc/fleet_runtime.py
git diff --check
```

Expected: all focused tests pass, Ruff reports no errors, and the diff check is clean.

- [ ] **Step 2: Verify the six physical execution boundaries**

Check all six configured ADB endpoints, unique UiAutomator system ports, TikTok
foreground/login state, proxy probes, and the single Appium server. Record the
result without printing account credentials or proxy material.

- [ ] **Step 3: Create the isolated database and round**

Import a deterministic 1,000-target slice from the source CSV into a new pool,
create a six-device round using the current distinct order seeds, and verify that
the new database contains exactly 6,000 assignments. Do not copy assignments,
action plans, quota windows, or control state from any existing database.

- [ ] **Step 4: Run the unchanged 15-minute baseline**

Start the six-device fleet against the isolated database, write five-minute
monitor samples, then stop it cleanly. Verify all worker and assignment leases
are released or recoverable from the durable checkpoint.

- [ ] **Step 5: Audit the baseline**

Report per-device and fleet throughput, stage mean/P90/max, confirmed and
uncertain actions, identity mismatches, completions lacking visits, quota use,
CPU, memory, ADB state, and Appium state. Explicitly compare interaction-bearing
assignments with effective trace assignments.

### Task 2: Deterministic Startup Phase Offset

Execute this task only when Task 1 shows synchronized route/identity command
inflation under six-device concurrency.

**Files:**
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `src/tikpoc/fleet.py`
- Test: `tests/test_fleet_runtime.py`
- Test: `tests/test_fleet.py`

- [ ] **Step 1: Write the failing runtime test**

Add a test with six device configs and an injected sleeper. Require each child
worker to apply its configured one-time startup offset before driver creation,
and prove that no sleeper is called between assignments.

- [ ] **Step 2: Run the test and observe the expected failure**

Run:

```bash
uv run pytest tests/test_fleet_runtime.py -q
```

Expected: the new startup-offset assertion fails because the configuration and
runtime do not yet expose the offset.

- [ ] **Step 3: Implement the minimal configuration and runtime behavior**

Add an optional nonnegative `startup_offset_ms` to each device configuration.
Apply it once in the child worker before Appium driver creation. Keep the default
at zero and do not add assignment-level delay, randomness, or shared locks.

- [ ] **Step 4: Verify focused behavior**

Run:

```bash
uv run pytest tests/test_fleet.py tests/test_fleet_runtime.py -q
uv tool run ruff check src/tikpoc/fleet.py src/tikpoc/fleet_runtime.py tests/test_fleet.py tests/test_fleet_runtime.py
uv tool run ruff format --check src/tikpoc/fleet.py src/tikpoc/fleet_runtime.py tests/test_fleet.py tests/test_fleet_runtime.py
```

Expected: all tests and checks pass.

- [ ] **Step 5: Commit the scheduling change**

```bash
git add src/tikpoc/fleet.py src/tikpoc/fleet_runtime.py tests/test_fleet.py tests/test_fleet_runtime.py
git commit -m "perf: stagger six-device worker startup"
```

### Task 3: Six-Device Comparison And Promotion

**Files:**
- Modify runtime artifact: `/Users/Shared/TikPoc/config/settings.six-capacity.yaml`
- Append runtime artifact: `/Users/Shared/TikPoc/six-capacity-monitor.ndjson`
- Modify: `docs/superpowers/specs/2026-07-22-six-device-daily-capacity-design.md`

- [ ] **Step 1: Configure deterministic offsets**

Use `0, 250, 500, 750, 1000, 1250` milliseconds for slots 1-6. Keep all other
device, proxy, Appium, action, timeout, and retry settings identical to Task 1.

- [ ] **Step 2: Run a fresh 15-minute comparison**

Use a fresh isolated round over the same 1,000-target pool. Stop cleanly at the
window boundary and retain all measurement artifacts.

- [ ] **Step 3: Apply the promotion gates**

Promote only if the fleet mean is at least 550 per device-hour, every device is
at least 400, projected 20-hour capacity reaches 10,000 per device, and all
identity/action/quota integrity checks pass. Revert the offset change if wall
throughput or integrity regresses.

- [ ] **Step 4: Run full verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
git diff --check
```

Expected: the full suite and all static checks pass.

- [ ] **Step 5: Record the measured result**

Append the exact baseline and comparison windows, per-device rates, stage
distributions, action evidence, acceptance decision, and durable test checkpoint
to the design document. Commit the measurement record separately from code.

### Task 4: Device-Local Proxy And Shard Readiness

**Files:**
- Modify: `src/tikpoc/proxy_guard.py`
- Modify: `tests/test_proxy_guard.py`
- Modify: `docs/proxy-guard-runbook.md`

- [x] **Step 1: Add a failing device-local VPN health test**

Require a configured VPN package, its worker process, `tun0`, and an Android
validated VPN network. A host listener or host-side HTTP probe must not mark the
device healthy when any required device-local signal is missing.

- [x] **Step 2: Add bounded Clash recovery**

When the configured Clash package is installed but its process or `tun0` is
missing, issue the documented start action once, wait once, and recheck. Preserve
the existing behavior for devices without device-local VPN configuration.

- [ ] **Step 3: Require a visible TikTok readiness probe**

Keep network health separate from business readiness. A loaded current-profile
or target-profile surface passes; a zero-metric profile with a visible loading
error fails. Do not clear app data, replace an account, or count this probe as a
confirmed target visit.

- [ ] **Step 4: Establish the execution-shard curve**

Run identical isolated windows with two, four, and six devices on one MYT host.
Record per-device and aggregate rates plus route, identity, metrics, video, and
action tails. Select the largest shard size whose slowest device sustains at
least 500 per hour for 30 minutes.

- [ ] **Step 5: Verify twelve-device deployment later**

When twelve devices are available, deploy independent shards according to the
measured host limit and run a 30-minute acceptance window. Do not infer this
result from the six-device test.
