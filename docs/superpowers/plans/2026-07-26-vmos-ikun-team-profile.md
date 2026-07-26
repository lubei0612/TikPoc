# VMOS IKUN Team Profile Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this operational plan inline. The user explicitly waived TDD because this is a one-time live account operation with no production-code change.

**Goal:** Change only the public nickname and avatar on `vmos-02` through `vmos-06`, leaving `vmos-01` and every TikTok `@username` unchanged.

**Architecture:** Use the existing local ADB/Appium path to inspect and operate TikTok's visible Edit profile UI one device at a time. Gate every write on exact visible account identity and verify the final public profile state rather than accepting command success alone.

**Tech Stack:** Android ADB, Appium/UiAutomator2, TikTok visible UI, ignored local screenshots

---

### Task 1: Apply and verify the five approved profiles

**Files:**
- Read: `config/vmos-six-catalog.local.yaml`
- Read: `config/vmos-six-catalog-identities.local.yaml`
- Runtime input: user-supplied PNG from the active conversation
- Runtime evidence: ignored `build/vmos-ikun-team-profile/`
- Production-code changes: none

- [ ] **Step 1: Establish the write boundary**

Confirm no publisher or touch worker owns ports `65286`, `59455`, `54178`,
`65511`, or `58659`. Do not connect to or open port `64860`.

- [ ] **Step 2: Stage the approved avatar**

Copy the user-supplied PNG into the ignored runtime evidence directory, push a
copy to each in-scope device media directory, and request a media scan. Do not
commit the image.

- [ ] **Step 3: Process each account through visible UI**

For each mapping below, open TikTok Profile, verify the exact `@username`, enter
Edit profile, change only the nickname, select the staged avatar with a centered
crop, and save:

| Device | Port | Required username | Nickname |
| --- | ---: | --- | --- |
| `vmos-02` | `65286` | `@saigeava6` | `IKUN Bags | Ava` |
| `vmos-03` | `59455` | `@yarazoey6` | `IKUN Bags | Zoey` |
| `vmos-04` | `54178` | `@helenasavannah` | `IKUN Bags | Savannah` |
| `vmos-05` | `65511` | `@gabriellaantonell37` | `IKUN Bags | Gabriella` |
| `vmos-06` | `58659` | `@kfuknocx78` | `IKUN Bags | Shop` |

Stop that device without edits on identity mismatch, login/verification prompt,
CAPTCHA, or account restriction. If either save is uncertain, inspect the public
profile before retrying.

- [ ] **Step 4: Verify terminal visible state**

Return to each public Profile page and verify the mapped nickname, unchanged
`@username`, and visibly changed common avatar. Save only redacted screenshots
and a status summary in the ignored runtime evidence directory.

- [ ] **Step 5: Report results**

Report verified nickname/avatar status separately for each device, explicitly
state that `vmos-01` was untouched, and list any failure or uncertain state
without claiming success from an ADB/Appium command alone.
