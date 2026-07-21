# Remote MYT ADB Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Keep the TikPoc fleet reachable when the operator Mac leaves the MYT LAN, then move device proxying to Clash Meta without changing business logic.

**Architecture:** Install Tailscale on the MYT-side host at `192.168.28.114` as a private subnet router for `192.168.28.0/24`; join the operator Mac to the same Tailnet; preserve the existing ADB/Appium endpoint configuration. Install Clash Meta per device only after remote ADB passes, with each device using a separately verified outbound identity. Vercel remains a later operator-console layer and never carries ADB/Appium traffic.

**Tech Stack:** Tailscale, Android Debug Bridge, Appium 3 / UiAutomator2, Clash Meta for Android, TikPoc Fleet, SQLite, launchd, optional Vercel CLI.

## Execution checkpoint — 2026-07-21

- The available MYT FRP client was used instead of installing a subnet router on
  the MYT host. Six mappings terminate on the VPS at ports `40000-40005`.
- The VPS and operator Mac joined the same Tailnet. The ignored remote Fleet
  configuration uses `100.101.215.87:40000-40005`.
- Public access to FRP ADB ports and the dashboard was removed with the VPS
  firewall; FRP control and Tailscale transport remain available.
- Clash Meta `2.11.31.Meta` was installed and configured on all six slots.
  Always-on VPN is configured, the legacy device HTTP proxies are empty, and
  visible TikTok profile loading passed on every slot.
- Slot 01 passed reboot restoration. Fleet was restarted against the remote
  Tailnet endpoints and resumed the same durable round with six healthy workers.
- Multiple node labels were assigned, but the measured egress identity was
  shared. Six independent exits remain a provider-capability gate.
- The final manual acceptance gate is moving the Mac to an unrelated Internet
  connection and observing the existing remote Fleet for at least ten minutes.

---

## Task 1: Freeze and record the production checkpoint

**Files:**
- Read: `/Users/Shared/TikPoc/tikpoc.db`
- Read: `config/settings.yaml`
- Create ignored backup: `/Users/Shared/TikPoc/checkpoints/remote-adb-2026-07-21/`
- Modify: none

- [ ] **Step 1: Verify the active round and source checksum**

Run:

```bash
sha256sum /Users/chenyuqi/Desktop/tik/all_users_deduped.csv
sqlite3 /Users/Shared/TikPoc/tikpoc.db \
  "select round_id,state from exposure_rounds order by created_at_ms desc limit 3;"
```

Expected: checksum `de40443a9079054df8c9c328d3ad969c5e10ee6cfc86ef5cc5f09b9526f82365`, and the active round is `round-ae3616f70853b1901e95`.

- [ ] **Step 2: Record durable counts and pause only before network changes**

```bash
curl -sS -X POST http://127.0.0.1:8766/api/commands/pause \
  -H 'Content-Type: application/json' \
  -d '{"command_id":"'"$(uuidgen)"'","scope":"round","scope_id":"round-ae3616f70853b1901e95"}'
sqlite3 /Users/Shared/TikPoc/tikpoc.db \
  "select phase,count(*) from round_assignments where round_id='round-ae3616f70853b1901e95' group by phase order by phase;"
```

Save the output in the checkpoint directory without storing credentials.

- [ ] **Step 3: Commit only the design and plan documentation**

```bash
git add docs/superpowers/specs/2026-07-21-remote-myt-adb-access-design.md \
        docs/superpowers/plans/2026-07-21-remote-myt-adb-access.md
git commit -m "docs: plan remote MYT ADB rollout"
```

## Task 2: Identify the MYT-side operating system and install Tailscale

**Files:**
- Read: `config/settings.yaml`
- Modify: MYT-side host service configuration only
- Test: local SSH and Tailscale status commands

- [ ] **Step 1: Discover the host OS without changing it**

```bash
ssh 192.168.28.114 'uname -a; (cat /etc/os-release || sw_vers) 2>/dev/null; command -v tailscale || true'
```

Use the existing administrator session or visible SSH authentication. Do not place passwords in shell history.

- [ ] **Step 2: Install Tailscale using the host-native package path**

For Debian/Ubuntu:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo systemctl enable --now tailscaled
sudo tailscale up --advertise-routes=192.168.28.0/24 --ssh
```

For macOS:

```bash
brew install --cask tailscale
sudo tailscale up --advertise-routes=192.168.28.0/24 --ssh
```

For Windows, install the signed Tailscale package through the visible administrator UI, then run in an elevated PowerShell:

```powershell
tailscale up --advertise-routes=192.168.28.0/24 --ssh
```

Do not approve the route until `tailscale status` shows the expected host identity.

- [ ] **Step 3: Verify subnet routing on the MYT-side host**

```bash
tailscale status
tailscale ip -4
```

The host must show an authenticated Tailnet address and the route advertisement must be visible in the Tailscale administration console for approval.

## Task 3: Join the operator Mac and verify remote ADB

**Files:**
- Modify: local Tailscale state and ignored local network configuration
- Read: `config/settings.yaml`
- Test: ADB and Appium connectivity

- [ ] **Step 1: Install and authenticate Tailscale on the Mac**

```bash
brew install --cask tailscale
open -a Tailscale
tailscale status
```

Approve the same Tailnet identity through the visible browser login flow.

- [ ] **Step 2: Approve the advertised route**

In the Tailscale administration console, approve `192.168.28.0/24` for the MYT-side node. Confirm from the Mac:

```bash
tailscale status
route -n get 192.168.28.114
```

- [ ] **Step 3: Verify all six ADB endpoints before changing the fleet**

```bash
ADB=/Users/chenyuqi/Library/Android/sdk/platform-tools/adb
$ADB kill-server
$ADB start-server
for p in 30000 30100 30200 30300 30400 30500; do
  $ADB connect 192.168.28.114:$p
done
$ADB devices -l
```

Expected: six `device` rows matching the existing slot mapping.

- [ ] **Step 4: Recreate six Appium sessions**

```bash
export ANDROID_HOME=/Users/chenyuqi/Library/Android/sdk
export ANDROID_SDK_ROOT=/Users/chenyuqi/Library/Android/sdk
npm run appium -- --address 127.0.0.1 --port 4723
```

Start the existing Fleet in the paused round, then confirm six active worker leases before resuming. Do not change system ports `8200-8205`.

- [ ] **Step 5: Test the route from an independent network**

Switch the Mac from the MYT LAN to a phone hotspot or another Internet connection. Repeat Step 3. The acceptance condition is six ADB devices reachable without the `192.168.28.144` LAN address.

If the independent-network test is not available in the current session, leave the round paused and record the exact remaining manual gate rather than claiming remote access.

## Task 4: Install and validate Clash Meta on one canary device

**Files:**
- Read: `/Users/chenyuqi/Desktop/tik/ClashMeta-2.11.31.apk`
- Modify: one Android device only
- Create ignored local input: `config/secrets/clash-subscription-url`
- Test: ADB package state, visible VPN state, public IP

- [ ] **Step 1: Verify the APK identity before installation**

```bash
sha256sum /Users/chenyuqi/Desktop/tik/ClashMeta-2.11.31.apk
/Users/chenyuqi/Library/Android/sdk/build-tools/37.0.0/aapt \
  dump badging /Users/chenyuqi/Desktop/tik/ClashMeta-2.11.31.apk | head -3
```

Expected package: `com.github.metacubex.clash.meta`, version `2.11.31.Meta`.

- [ ] **Step 2: Install on slot 01 without printing subscription data**

```bash
ADB=/Users/chenyuqi/Library/Android/sdk/platform-tools/adb
$ADB -s 192.168.28.114:30000 install -r \
  /Users/chenyuqi/Desktop/tik/ClashMeta-2.11.31.apk
$ADB -s 192.168.28.114:30000 shell pm path com.github.metacubex.clash.meta
```

- [ ] **Step 3: Import the subscription through visible UI**

Open the application visibly on slot 01, paste the value from the ignored local secret file, save the profile, grant VPN consent, select the device-specific node, and enable the foreground VPN. Do not use shell arguments containing the URL.

- [ ] **Step 4: Validate restart recovery and outbound identity**

```bash
$ADB -s 192.168.28.114:30000 shell am force-stop com.github.metacubex.clash.meta
$ADB -s 192.168.28.114:30000 shell monkey -p com.github.metacubex.clash.meta 1
$ADB -s 192.168.28.114:30000 shell dumpsys vpn_management 2>/dev/null || true
```

Use TikTok visible UI and an external IP check from the device to confirm network access. Record only pass/fail and a redacted node label.

## Task 5: Roll Clash Meta out to the remaining five devices

**Files:**
- Modify: slots 02–06 device state
- Read: `config/settings.yaml`
- Test: six-device proxy and TikTok connectivity

- [ ] **Step 1: Install sequentially**

Install the same verified APK with ADB on ports `30100`, `30200`, `30300`, `30400`, and `30500`. Wait for package installation to finish before moving to the next device.

- [ ] **Step 2: Configure unique node assignments**

Use the visible Clash Meta UI. Assign separate node labels to account/device pairs and verify that no two devices unintentionally use the same exit identity.

- [ ] **Step 3: Reboot and validate each device**

For each slot, force-stop and relaunch Clash Meta, confirm the VPN is active, open TikTok, and perform one visible profile load. Keep the old Mac relay configuration available until all six pass.

## Task 6: Remote endurance canary and resume

**Files:**
- Read: `/Users/Shared/TikPoc/tikpoc.db`
- Read: `docs/mobile-fleet-runbook.md`
- Modify: none unless a verified configuration update is required

- [ ] **Step 1: Start Fleet only after both network and proxy gates pass**

```bash
curl -sS -X POST http://127.0.0.1:8766/api/commands/start \
  -H 'Content-Type: application/json' \
  -d '{"command_id":"'"$(uuidgen)"'","scope":"round","scope_id":"round-ae3616f70853b1901e95"}'
```

- [ ] **Step 2: Run a ten-minute independent-network canary**

Every minute, record:

```bash
sqlite3 /Users/Shared/TikPoc/tikpoc.db \
  "select phase,count(*) from round_assignments where round_id='round-ae3616f70853b1901e95' group by phase order by phase;"
adb devices -l
```

Acceptance requires six active worker leases, forward progress on all six devices, no account/device mismatch, no Inbox navigation, and no duplicate action for one plan/video.

- [ ] **Step 3: Keep the round running after the canary**

If all gates pass, leave the same durable round running. If any gate fails, pause the round, recover only expired leases, restore the last working route, and preserve the checkpoint.

## Task 7: Optional Vercel operator console (separate deployment)

**Files:**
- Read: `src/tikpoc/dashboard.py`
- Read: `src/tikpoc/api.py`
- Modify: only a new frontend/deployment adapter after remote ADB passes
- Test: Vercel preview health and authenticated control API

- [ ] **Step 1: Keep the local dashboard as execution truth**

Do not point Vercel directly at SQLite or private ADB. The local controller remains the only owner of leases and device commands.

- [ ] **Step 2: Build a read-only Vercel view first**

Deploy only metrics and health through an authenticated outbound API. Verify that no secret, cookie, subscription, ADB serial, or local filesystem path is exposed to the browser.

- [ ] **Step 3: Add control actions only with durable command IDs**

If control is later enabled, each start/pause/stop/retry operation must reuse the existing idempotent command model and require an authenticated operator session.

## Verification Matrix

Run after each applicable task:

```bash
adb devices -l
curl -sS http://127.0.0.1:4723/status
uv run pytest tests/test_appium_device.py tests/test_mobile_worker.py tests/test_fleet_runtime.py -q
uv tool run ruff check src tests
git diff --check
```

The remote-access rollout is complete only after the independent-network canary passes. A local-network ADB test alone is not sufficient evidence.
