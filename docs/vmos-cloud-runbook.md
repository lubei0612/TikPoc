# VMOS Cloud Device Bootstrap Runbook

This runbook records the verified fast path for preparing a VMOS Cloud device
for the mobile touch worker. Keep ADB bridge credentials and proxy credentials
in ignored files under `config/secrets/`; use placeholders in commands, logs,
commits, and screenshots.

## Verified baseline

- Android device is reachable through the VMOS SSH-to-ADB bridge.
- `V2rayNG_2.2.6.apk` provides the per-device VPN.
- The supplied authenticated endpoints use SOCKS5.
- DNS UDP traffic goes through `direct`; ordinary application traffic goes
  through the selected SOCKS5 `proxy` outbound.
- The public exit is verified in Chrome before TikTok is opened.
- Use regular TikTok (`com.zhiliaoapp.musically`) for this workflow. TikTok
  Lite has a separate region/service-availability check and is not the runtime
  acceptance target.

## 1. Establish the ADB bridge

Copy the current SSH command, connection password, local ADB port, and expiry
time from the VMOS ADB panel. Store the password in an owner-only ignored file.

```bash
chmod 600 /tmp/vmos-adb-password

expect <<'EOF'
set timeout -1
set f [open "/tmp/vmos-adb-password" r]
set pw [string trim [read $f]]
close $f
spawn ssh \
  -oStrictHostKeyChecking=accept-new \
  -oExitOnForwardFailure=yes \
  -oServerAliveInterval=20 \
  -oServerAliveCountMax=3 \
  ADB_USER@ADB_HOST -p ADB_SSH_PORT \
  -L LOCAL_ADB_PORT:localhost:1 -N
expect {
  -re "(?i)password:" { send -- "$pw\r"; exp_continue }
  eof
}
EOF
```

In another terminal:

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
"$ADB" connect localhost:LOCAL_ADB_PORT
"$ADB" devices -l
"$ADB" -s localhost:LOCAL_ADB_PORT get-state
```

Acceptance: the serial is listed as `device`, not `offline`. After a VMOS
reset or ADB toggle, rebuild the SSH tunnel before debugging Android packages.

## 2. Validate and select a proxy

Keep one proxy per line in the ignored file
`config/secrets/vmos-proxies.txt` using:

```text
HOST:PORT:USERNAME:PASSWORD
```

Test the endpoint from the controller before entering it on the phone:

```bash
curl --max-time 7 \
  --proxy 'socks5h://USERNAME:PASSWORD@HOST:PORT' \
  https://ipwho.is/
```

Record only country, city, latency, and public IP in operational notes. Select
a responsive endpoint in the desired country; do not print its credentials.

## 3. Install V2rayNG and the reliable text-entry helper

```bash
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SERIAL=localhost:LOCAL_ADB_PORT

"$ADB" -s "$SERIAL" install -r /path/to/V2rayNG_2.2.6.apk
```

Android's localized physical-key input can rewrite periods and hyphens. For
repeatable ADB-driven form entry, install the open-source ADBKeyBoard helper,
temporarily select it, and use base64 broadcasts:

```bash
"$ADB" -s "$SERIAL" install -r /path/to/ADBKeyboard.apk
"$ADB" -s "$SERIAL" shell ime enable com.android.adbkeyboard/.AdbIME
"$ADB" -s "$SERIAL" shell ime set com.android.adbkeyboard/.AdbIME

VALUE_B64="$(printf %s 'VALUE' | base64)"
"$ADB" -s "$SERIAL" shell am broadcast -a ADB_CLEAR_TEXT
"$ADB" -s "$SERIAL" shell am broadcast -a ADB_INPUT_B64 --es msg "$VALUE_B64"
```

Restore Gboard after configuration:

```bash
"$ADB" -s "$SERIAL" shell ime set \
  com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME
```

## 4. Create the SOCKS profile

In V2rayNG:

1. Open **Add configuration**.
2. Choose **Add [SOCKS]**.
3. Enter a non-secret alias, `HOST`, `PORT`, `USERNAME`, and `PASSWORD`.
4. Save the profile.
5. Reopen it once and verify the exact host and port. Verify only that username
   and password are present; never print them.

Do not trust the status-bar carrier label as exit evidence.

## 5. Add the required DNS route

Authenticated SOCKS5 endpoints in this setup carry TCP traffic but do not
provide the UDP DNS behavior expected by V2rayNG's default profile. Without the
route below, V2rayNG reports connected while Chrome reports a DNS failure.

In **Routing settings**, add and enable a rule before starting the VPN:

```text
remarks: DNS-servers-direct
ip: 1.1.1.1,8.8.8.8,8.8.4.4
outboundTag: direct
```

Save, reopen the rule, and confirm its displayed outbound is `direct`. Restart
the V2rayNG service so the generated runtime configuration is refreshed.

Diagnostic evidence should contain both patterns:

```text
udp:DNS_IP:53 [socks -> direct]
tcp:DESTINATION:443 [socks -> proxy]
```

If DNS still shows `[socks -> proxy]`, the route is missing, disabled, saved
with the wrong outbound, or the VPN was not restarted.

## 6. Verify the real public exit

Open a simple endpoint in Chrome because root ADB shell traffic may bypass the
Android VPN:

```bash
"$ADB" -s "$SERIAL" shell am force-stop com.android.chrome
"$ADB" -s "$SERIAL" shell am start \
  -a android.intent.action.VIEW \
  -d 'https://api.ipify.org?format=json' \
  com.android.chrome
```

Acceptance:

- Chrome displays the expected proxy public IP.
- `ipwho.is` reports the intended country and city.
- V2rayNG logs show DNS direct and HTTPS proxy routing.

## 7. Prepare regular TikTok

Keep TikTok Lite stopped and start regular TikTok from a clean application
state when preparing a new account:

```bash
"$ADB" -s "$SERIAL" shell am force-stop com.ss.android.ugc.tiktok.lite
"$ADB" -s "$SERIAL" shell pm clear com.zhiliaoapp.musically
"$ADB" -s "$SERIAL" shell monkey \
  -p com.zhiliaoapp.musically \
  -c android.intent.category.LAUNCHER 1
```

Acceptance: the visible screen offers the regular TikTok login flow. Account
login remains a separate visible-state step.

## Fast repeat checklist

1. Refresh VMOS ADB and rebuild the SSH tunnel.
2. Confirm `adb get-state` returns `device`.
3. Test candidate SOCKS5 endpoint from the controller.
4. Install V2rayNG and select ADBKeyBoard temporarily.
5. Create the SOCKS profile using base64 text entry.
6. Add and verify `DNS-servers-direct`.
7. Restart V2rayNG.
8. Verify the public IP in Chrome.
9. Restore Gboard English input.
10. Open regular TikTok and verify the visible login page.

