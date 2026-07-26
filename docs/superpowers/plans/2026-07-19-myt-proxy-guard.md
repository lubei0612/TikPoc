# MYT Proxy Guard Implementation Plan

> **历史文档：** MYT 已退出当前生产运行。本文只保留为实测、回滚和兼容证据；新设备与任务使用 VMOS 自主 HTTPS APK 路径。


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every configured MYT device on the existing Clash Verge subscription by continuously reconciling the Mac LAN endpoint and Android global proxy state.

**Architecture:** A focused `proxy_guard.py` module owns address discovery, local listener recovery, ADB reconciliation, and redacted health results through injected boundaries. The CLI loads `FleetConfig` and runs one cycle or a bounded-sleep loop. A user LaunchAgent keeps the command alive after Mac login while Clash Verge remains the sole subscription owner.

**Tech Stack:** Python 3.12+, standard-library socket/subprocess/time, existing `FleetConfig`, pytest, launchd.

---

## File Structure

- Create `src/tikpoc/proxy_guard.py`: pure orchestration, typed health rows, ADB command boundary, local proxy recovery.
- Create `tests/test_proxy_guard.py`: unit coverage with injected address/listener/process behavior.
- Modify `src/tikpoc/cli.py`: `proxy-guard` parser and command dispatch.
- Modify `tests/test_cli.py`: CLI delegation and validation tests.
- Create `launchd/com.tikpoc.proxy-guard.plist`: committed installation example.
- Modify `docs/mobile-fleet-runbook.md`: install, inspect, and recovery commands.
- Modify `AGENTS.md`: verified proxy checkpoint.

### Task 1: Proxy Reconciliation Core

**Files:**
- Create: `src/tikpoc/proxy_guard.py`
- Create: `tests/test_proxy_guard.py`

- [ ] **Step 1: Write failing healthy and DHCP-change tests**

Define tests around this public contract:

```python
guard = ProxyGuard(
    config,
    adb_path=Path("/sdk/adb"),
    source_address=lambda _host: "192.0.2.20",
    listener_probe=lambda _host, _port: True,
    runner=fake_runner,
)
rows = guard.reconcile()
assert [row.proxy_state for row in rows] == ["healthy", "corrected"]
assert all("subscription" not in repr(row).lower() for row in rows)
```

The fake runner must prove a matching device receives no `settings put`, while
an old DHCP address receives the combined and split Android proxy writes.

- [ ] **Step 2: Run the tests and observe the missing module failure**

Run: `uv run pytest tests/test_proxy_guard.py -q`

Expected: collection fails because `tikpoc.proxy_guard` does not exist.

- [ ] **Step 3: Implement the typed guard and ADB reconciliation**

Create:

```python
@dataclass(frozen=True)
class ProxyHealth:
    device_id: str
    adb_state: str
    proxy_state: str
    http_status: int | None

class ProxyGuard:
    def reconcile(self) -> tuple[ProxyHealth, ...]: ...
```

Resolve the current Mac source address, require the local mixed proxy to be
listening, reconnect each ADB endpoint, read `http_proxy` plus split host/port,
and write all three fields only on mismatch. Catch each device command failure
and return a redacted unhealthy row without including stderr.

- [ ] **Step 4: Add failing listener-recovery and device-isolation tests**

Cover a listener probe sequence of `False, True` and assert exactly one call to:

```python
("open", "-gja", "-a", "Clash Verge")
```

Also make device 1 raise `CalledProcessError(stderr="SUBSCRIPTION_URL")` and
assert device 2 still reconciles and no health representation contains stderr.

- [ ] **Step 5: Implement bounded Clash recovery and HTTP probing**

When the first listener probe fails, open Clash Verge, sleep only through the
injected sleeper, and probe once more. When available, execute Android curl with
an explicit proxy and output only `%{http_code}`. Treat missing curl as unknown.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_proxy_guard.py -q
uv tool run ruff check src/tikpoc/proxy_guard.py tests/test_proxy_guard.py
uv tool run ruff format --check src/tikpoc/proxy_guard.py tests/test_proxy_guard.py
```

Commit only the new module and its tests:

```bash
git add src/tikpoc/proxy_guard.py tests/test_proxy_guard.py
git commit -m "feat: reconcile MYT device proxies"
```

### Task 2: Proxy Guard CLI

**Files:**
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write a failing CLI delegation test**

```python
def test_cli_proxy_guard_runs_one_cycle(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run_proxy_guard", fake_run)
    assert main(["proxy-guard", "--devices", str(config), "--once"]) == 0
    assert "devices=2 healthy=2 corrected=0 failed=0" in capsys.readouterr().out
```

Also assert a missing device configuration fails before delegation and an
interval below five seconds is rejected.

- [ ] **Step 2: Run the CLI tests and observe parser failure**

Run: `uv run pytest tests/test_cli.py -k proxy_guard -q`

Expected: argparse reports `proxy-guard` as an invalid command.

- [ ] **Step 3: Add parser and command loop**

Register:

```python
proxy_guard = commands.add_parser("proxy-guard")
proxy_guard.add_argument("--devices", type=Path, required=True)
proxy_guard.add_argument("--adb-path", type=Path)
proxy_guard.add_argument("--interval", type=float, default=30.0)
proxy_guard.add_argument("--once", action="store_true")
```

Load `FleetConfig`, delegate through `_run_proxy_guard`, print aggregate redacted
counts with `flush=True`, and repeat through an interruptible sleep unless
`--once` is present. Return `130` on `KeyboardInterrupt`.

- [ ] **Step 4: Run focused CLI and guard tests**

Run: `uv run pytest tests/test_cli.py -k proxy_guard -q && uv run pytest tests/test_proxy_guard.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit CLI behavior**

```bash
git add src/tikpoc/cli.py tests/test_cli.py
git commit -m "feat: run persistent proxy health guard"
```

### Task 3: LaunchAgent And Live Recovery Gate

**Files:**
- Create: `launchd/com.tikpoc.proxy-guard.plist`
- Modify: `docs/mobile-fleet-runbook.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Create the committed LaunchAgent example**

Use `RunAtLoad`, `KeepAlive`, a 10-second throttle, the primary checkout, and:

```text
tikpoc proxy-guard --devices /Users/chenyuqi/Desktop/tik/config/settings.yaml --interval 30
```

Write stdout/stderr under `~/Library/Logs/TikPoc/`. Do not add subscription or
provider values.

- [ ] **Step 2: Validate and install the active-worktree LaunchAgent**

Run `plutil -lint launchd/com.tikpoc.proxy-guard.plist`, create an installed copy
pointing to the active worktree, then use `launchctl bootstrap` and verify its
state is `running`.

- [ ] **Step 3: Run baseline six-device acceptance**

Run:

```bash
uv run tikpoc proxy-guard --devices config/settings.yaml --once
```

Expected: six reachable devices, six matching settings, six TikTok HTTP `200`
results, and no corrective writes on the second consecutive cycle.

- [ ] **Step 4: Run controlled restoration acceptance**

Temporarily set slot 6 to a synthetic stale proxy endpoint, run one guard cycle,
and verify slot 6 returns to the current Mac address and `7897`. Run a second
cycle and verify six healthy results. Do not change TikTok UI or account state.

- [ ] **Step 5: Document operations and checkpoint**

Document launchctl status, one-shot health, log inspection, and uninstall
commands. Update `AGENTS.md` with measured evidence and remaining login gates.

- [ ] **Step 6: Run regression and commit**

Run:

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src/tikpoc/proxy_guard.py src/tikpoc/cli.py tests/test_proxy_guard.py tests/test_cli.py
plutil -lint launchd/com.tikpoc.proxy-guard.plist
git diff --check
```

Commit only guard launch/runtime documentation files, preserving unrelated
concurrent changes:

```bash
git add launchd/com.tikpoc.proxy-guard.plist docs/mobile-fleet-runbook.md AGENTS.md
git commit -m "ops: keep six-device proxy routing healthy"
```

