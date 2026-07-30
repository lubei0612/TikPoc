# VMOS Device-Side Touch Executor Runbook

## Build And Configure

Build the helper from repository source:

```bash
bash android-touch-executor/build.sh
```

Create an ignored local fleet file with one canary entry:

```yaml
devices:
  - device_id: VMOS_CANARY
    account_id: ACCOUNT_ID
    myt_slot: 1
    backend: device-side
    adb_endpoint: ADB_ENDPOINT
    helper_host_port: 47101
    helper_device_port: 47101
    order_seed: UNIQUE_SEED
```

Keep the existing `myt` and `proxy_relay` top-level sections required by the
current fleet configuration schema. Do not store VMOS credentials, proxy
configuration, TikTok credentials, or target data in the helper APK or a
committed fleet file.

The helper commands use `adb` from `PATH` when available and otherwise check
`ANDROID_HOME`, `ANDROID_SDK_ROOT`, and the default macOS Android SDK path.
Register the tunnel with the same IP-form serial used in the fleet file (for
example, connect with `127.0.0.1:PORT` when the fleet endpoint uses that form);
ADB treats `localhost:PORT` as a different serial.

## Install And Health

Install the APK and inspect service state:

```bash
uv run tikpoc helper-bootstrap \
  --fleet config/vmos-device-side.local.yaml \
  --device-id VMOS_CANARY \
  --apk android-touch-executor/build/touch-executor.apk
```

When `visible_enablement_required` is true, open Android Settings on the VMOS
canary and visibly enable **TikPoc Touch Executor** under Accessibility. Keep
TikTok in the foreground and run:

```bash
uv run tikpoc helper-health \
  --fleet config/vmos-device-side.local.yaml \
  --device-id VMOS_CANARY
```

Acceptance requires `service_enabled=true`, `tiktok_foreground=true`,
`busy=false`, helper version `1.0.0`, and a current TikTok surface. The command
creates a serial-scoped ADB forward and removes it before exit.

### Safe In-Place Upgrade

ADB is a provisioning and diagnostic channel only; the installed worker pulls
and reports tasks over HTTPS. For an in-place APK upgrade:

1. Pause the device control and wait for the current task to become terminal.
2. Run `adb -s ADB_ENDPOINT install -r touch-executor.apk`.
3. Launch `com.tikpoc.touch/.ProvisioningActivity` once so Android clears the
   package stopped state.
4. Visibly disable and re-enable **TikPoc Touch Executor** in Accessibility.
5. Resume only after the server receives a fresh HTTPS heartbeat and reports
   the worker as idle or running.

Do not force-stop the package after enabling Accessibility: that leaves the
package stopped and prevents Android from binding the service. Treat an ADB
disconnect after step 5 as non-fatal when HTTPS heartbeats and task progress
continue. Reopen ADB only for the next install or a bounded diagnostic.

## Canary Gates

Use a fresh ignored SQLite database and a fresh round at every gate. Do not
change eligibility, plans, action quotas, retries, or interaction verification
to improve throughput.

1. Run 20 targets. Require 20/20 confirmed visits, exact identity/action
   audits, no duplicates or quota violations, and at least 400 confirmed/hour.
2. Run 100 representative targets. Require 100/100 confirmed visits and at
   least 500 confirmed/hour.
3. Run an uninterrupted 30-minute sample with enough queued work to avoid idle
   time. Require at least 500 confirmed/hour, projected 10,000 in 20 productive
   hours, mean below 6.5 seconds, and P90 below 8.64 seconds.
4. Expand to two devices only after the single-device gates pass. Give every
   device a unique ADB endpoint, account, fence, helper host port, and order
   seed. Then expand incrementally to the configured active account count.

Report measured throughput separately from projected 20-hour capacity. Keep
stage/helper timing distributions and fallback counts, but exclude target IDs
and visible content from aggregate reports.

## Correctness Audit

For each gate verify from durable SQLite state:

- every confirmed visit has exact visible username evidence;
- each assignment, immutable plan, and action attempt index is unique;
- like/favorite/repost actions have visible before/after evidence;
- repost includes the visible repost control and resulting-state verification;
- `uncertain` receives one read-only reconciliation and no immediate second
  click;
- due interactions were not converted to trace for speed;
- coverage equals the configured active account count for the logical batch.

## Stop And Roll Back

Stop the device-side worker before changing backend. Confirm its process exited
and its ADB forward was removed:

```bash
adb -s ADB_ENDPOINT forward --list
```

Visibly disable **TikPoc Touch Executor** when the helper is no longer in use.
Set that device's backend to `appium`, restore its `appium_url`, and resume the
same durable round. Confirm the existing assignment phase and immutable action
plan were retained and that no duplicate action attempt was created.

Roll back when any correctness audit regresses, the 20-target gate remains
below 400/hour, or the 100-target gate remains below 500/hour. Preserve the
failed database and redacted report as measured evidence.
