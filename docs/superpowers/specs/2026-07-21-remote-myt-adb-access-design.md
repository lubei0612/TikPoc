# Remote MYT ADB Access Design

> **历史文档：** MYT 已退出当前生产运行。本文只保留为实测、回滚和兼容证据；新设备与任务使用 VMOS 自主 HTTPS APK 路径。


**Date:** 2026-07-21  
**Status:** Implemented through remote Fleet canary; independent-network handoff remains  
**Scope:** Keep TikPoc running when the operator Mac leaves the MYT LAN

## 1. Problem

The current operator Mac and MYT gateway share the `192.168.28.0/24` LAN:

- operator Mac: `192.168.28.144`;
- MYT gateway: `192.168.28.114`;
- device ADB endpoints: `192.168.28.114:30000-30500`;
- TikPoc Fleet, Appium, SQLite, dashboard, and proxy relay currently run on the Mac.

When the Mac leaves this LAN, it loses the route to the MYT ADB endpoints. The current proxy relay also disappears from the devices. Worker leases then expire and all device work stops even though durable round state remains intact.

## 1.1 Implemented Transport Decision

The deployed path supersedes the original subnet-router proposal below. The MYT
gateway already provides an FRP client, so the accepted production path is:

```text
Android slot ADB 30000-30500 -> MYT FRP client
  -> fixed VPS FRPS (public control 7000)
  -> Tailnet 100.101.215.87:40000-40005
  -> operator Mac + Appium + TikPoc Fleet
```

The VPS firewall keeps FRP ADB ports and the dashboard available through
`tailscale0` while leaving only SSH, FRP control, and Tailscale transport on the
public interface. The ignored remote Fleet configuration maps durable device IDs
to the Tailnet endpoints without changing account, Appium, round, or coverage
identities.

## 2. Goals

1. Let the Mac reach all six MYT ADB endpoints from another Internet connection.
2. Keep ADB ports private rather than exposing them directly to the public Internet.
3. Preserve the existing database, round, account mapping, device mapping, and Appium system ports.
4. Move device Internet proxying away from the Mac by running Clash Meta locally on each device.
5. Prove that the fleet continues processing after the Mac switches away from the MYT LAN.
6. Keep the design compatible with a later migration of the controller to an always-on node.

## 3. Non-goals

- Vercel will not run ADB, Appium, Fleet workers, SQLite, or Android proxy services.
- This change will not migrate SQLite to Supabase.
- This change will not alter eligibility, interaction selection, verification, quotas, idempotency, or coverage accounting.
- This change will not expose ports `30000-30500` through ordinary router port forwarding.
- This change will not move the full TikPoc runtime to `192.168.28.114` in the first phase.

## 4. Selected Architecture

Use Tailscale as a private overlay network.

`192.168.28.114` will join the same Tailnet as the operator Mac and advertise the MYT LAN as a subnet route:

```text
192.168.28.0/24
```

The Mac will keep using the current ADB endpoint names and ports. When it is on another network, Tailscale will route `192.168.28.0/24` through the fixed MYT-side node.

```text
Six Android devices
        |
        | MYT local device transport
        v
192.168.28.114
MYT gateway + Tailscale subnet router
        |
        | encrypted Tailnet path
        v
Operator Mac
TikPoc Fleet + Appium + SQLite + dashboard
```

The Mac must remain powered, connected to the Internet, and awake. A later phase may move Fleet and Appium to the fixed node so work continues even when the Mac is powered off.

## 5. Device Proxy Architecture

Each Android device will run the local package:

```text
com.github.metacubex.clash.meta
```

The inspected APK is `ClashMeta-2.11.31.apk`, version `2.11.31.Meta`.

Each device requires:

1. one installed application instance;
2. an imported subscription stored only on that device or in ignored local configuration;
3. Android VPN consent;
4. foreground service and boot restoration enabled;
5. battery optimization disabled where supported;
6. a device-specific proxy node or verified distinct outbound identity;
7. a restart test proving the VPN returns automatically.

Subscription URLs, tokens, node credentials, and device public IPs must not enter Git, committed docs, test fixtures, command output, or application logs.

### Implemented device state

All six devices now have the verified APK installed, the subscription imported,
the Clash VPN running, Android Always-on VPN configured, background restrictions
relaxed, and the legacy global HTTP proxy removed. Slot 01 passed a reboot test:
ADB returned, Always-on remained configured, the global proxy stayed empty, and
the Clash VPN network returned automatically. Visible TikTok profile loads passed
on all six devices after removing the Mac relay dependency.

The configured subscription exposes multiple selectable node labels, but the six
tested selections currently resolve to one shared egress identity. This matches
the previous upstream behavior and does not block remote connectivity, but it is
not evidence of six independent public exits. Independent exits require a
provider plan that actually supplies distinct egress identities.

## 6. Vercel Boundary

Vercel may later host a static/operator web frontend. It will not connect directly to private ADB endpoints.

Any future remote dashboard deployment must communicate with a local TikPoc control service through an authenticated outbound channel. That work is separate from the Tailscale and Clash Meta rollout because it introduces remote command authentication, replay protection, audit logs, and deployment configuration.

## 7. Network and Access Controls

1. ADB and SSH remain reachable only through the LAN or Tailnet.
2. Tailscale ACLs should allow the operator identity to reach:
   - SSH on the MYT-side host;
   - ADB ports `30000-30500`;
   - any explicitly required MYT administration port.
3. No wildcard public ingress is added.
4. Tailnet route approval is performed through the Tailscale administration interface.
5. The MYT-side node must enable IP forwarding required for subnet routing.
6. Existing local access from `192.168.28.144` remains available as a rollback path.

## 8. Rollout Sequence

### Phase 1: Preserve the running round

- Record the current round state and assignment counts.
- Pause the round before changing ADB routing.
- Keep SQLite and configuration backups outside Git.
- Do not change account or device identifiers.

### Phase 2: Establish the Tailnet path

- Identify the operating system on `192.168.28.114`.
- Install Tailscale on the MYT-side host and operator Mac.
- Authenticate both devices into the same Tailnet.
- Advertise and approve `192.168.28.0/24`.
- Verify SSH and all six TCP ADB ports over the Tailnet path.

### Phase 3: Test remote ADB and Appium

- Stop the local ADB server and reconnect all six endpoints.
- Switch the Mac from the MYT LAN to an independent network such as a phone hotspot.
- Confirm `adb devices -l` reports the same six serials.
- Recreate all six Appium sessions using system ports `8200-8205`.
- Confirm exact device/account mappings before resuming work.

### Phase 4: Move proxying onto devices

- Install the inspected Clash Meta APK on one canary device.
- Import its subscription without printing it.
- grant VPN consent through visible device UI;
- verify TikTok access and the expected outbound identity;
- restart the canary and verify automatic restoration;
- repeat sequentially for the remaining five devices;
- remove the device's dependency on the Mac proxy relay only after its local VPN passes.

### Phase 5: Remote endurance canary

- Resume the same durable round from its checkpoint.
- Run for at least ten minutes while the Mac remains off the MYT LAN.
- Verify six active worker leases, forward progress, Appium stability, proxy stability, no Inbox navigation, no duplicated video action, and no account/device mismatch.
- Keep the local LAN route and previous proxy settings available for rollback until the canary passes.

## 9. Failure Handling

### Tailnet route unavailable

- Persistently pause the round.
- Stop Fleet workers before their assignment leases are reused.
- Reconnect through the original LAN or restore the previous route.
- Recover only expired assignment leases and resume the same round.

### Individual ADB endpoint unavailable

- Pause only the affected device when possible.
- Compare the failed endpoint against the other five endpoints.
- Reconnect its ADB serial and recreate only its Appium session.
- Preserve the device/account fence and assignment checkpoint.

### Clash Meta unavailable

- Pause the affected device.
- Restore its previous proxy path or repair its local VPN.
- Verify TikTok network access before returning it to the fleet.
- Do not compensate by switching the account onto another device.

### Mac sleep or power loss

The first phase still depends on the Mac runtime. The operating system must prevent sleep during active rounds. Full tolerance of Mac power loss requires the later controller migration to an always-on node.

## 10. Acceptance Gates

The rollout is accepted only when all conditions pass:

1. The Mac is connected through an independent network, not the MYT LAN.
2. `adb devices -l` shows all six expected serials as `device`.
3. Appium creates six sessions with the existing device and system-port mapping.
4. Each device has working TikTok connectivity through its local Clash Meta VPN.
5. Device outbound identities match the configured per-account plan.
6. Six worker leases remain active and renew normally.
7. The durable round continues from the recorded checkpoint.
8. A ten-minute remote canary shows forward progress on all six devices.
9. No Inbox route, duplicate interaction, false completion, or account mismatch is observed.
10. The original LAN route and proxy path can be restored without changing round identity.

## 11. Later Controller Migration

After the remote-access canary, the preferred long-term architecture is to run Fleet, Appium, SQLite, monitoring, and proxy supervision on an always-on node in the MYT network. The Mac then becomes only a remote operator console. That migration requires its own design because it changes service supervision, filesystem locations, backup ownership, database locking, and deployment procedures.
