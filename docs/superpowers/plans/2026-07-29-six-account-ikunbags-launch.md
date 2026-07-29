# Six-Account IKUN Bags Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brand six newly logged-in VMOS TikTok accounts as an IKUN Bags matrix and visibly publish fifteen curated bag posts per account.

**Architecture:** Treat each VMOS endpoint as an exclusive device/account pair. Build one durable manifest containing device identity, assigned nickname, fifteen curated products, six caption variants per product, and visible profile-grid confirmation for every publish. Stop at identity, CAPTCHA, restriction, or uncertain-result boundaries instead of retrying blindly.

**Tech Stack:** VMOS Cloud, Android Debug Bridge, Appium 2, TikTok Android visible UI, Python manifest utilities, GXHY public shop pages.

---

### Task 1: Restore And Inventory The Six Devices

**Files:**
- Create: `build/ikunbags-six-account/device-inventory.json`
- Reference: `docs/superpowers/specs/2026-07-29-six-account-ikunbags-launch-design.md`

- [ ] **Step 1: Confirm six VMOS ADB tunnels are online**

Run:

```bash
/Users/chenyuqi/Library/Android/sdk/platform-tools/adb devices -l
```

Expected: six distinct `device` rows. An `offline` row is not accepted.

- [ ] **Step 2: Confirm no existing publisher owns a device**

Run:

```bash
ps aux | rg 'tikpoc.*publish|apply_profiles|appium' | rg -v rg
```

Expected: no active profile/publisher worker. Existing idle Appium servers may be reused only with unique system ports.

- [ ] **Step 3: Read each visible public profile identity**

For every online endpoint, open the TikTok profile through visible UI, scroll to the full header, and record the exact public username. Reject duplicate usernames or an account that cannot expose a stable profile header.

- [ ] **Step 4: Save the inventory**

Write ignored runtime evidence to `build/ikunbags-six-account/device-inventory.json` with `device_id`, `adb_endpoint`, `username`, `nickname_before`, and `verified_at`.

### Task 2: Apply The IKUN Bags Profile Matrix

**Files:**
- Create: `build/ikunbags-six-account/profile-results.json`
- Reuse: `build/vmos-ikun-team-profile/ikun-team-avatar.png`

- [ ] **Step 1: Assign nicknames in device order**

Use exactly:

```text
IKUN Bags | Ava
IKUN Bags | Zoey
IKUN Bags | Mia
IKUN Bags | Lily
IKUN Bags | Chloe
IKUN Bags | Shop
```

- [ ] **Step 2: Change only the public nickname**

Open Edit Profile, re-read the immutable username, enter the assigned nickname, save, handle only the ordinary confirmation dialog, and verify the assigned nickname plus original username are visible together. Record cooldown text instead of bypassing it.

- [ ] **Step 3: Upload the shared avatar safely**

Select the staged avatar, verify its preview, disable `发布此照片到限时动态`, verify the final button reads `保存` rather than `保存并发布`, save, and wait until `正在上传...` disappears.

- [ ] **Step 4: Capture profile evidence**

Save one screenshot per device showing the new avatar, nickname, and original username in Edit Profile. Record each result in `profile-results.json`.

### Task 3: Curate And Stage Ninety Distinct Products

**Files:**
- Create: `build/ikunbags-six-account/products.json`
- Create: `build/ikunbags-six-account/media/<product-id>/`

- [ ] **Step 1: Load the supplied GXHY shop**

Open:

```text
https://gxhy1688.com/Shopindex?marketCode=gz&uid=0cc703016c964d21b1aed580e59b2247
```

Expected: public shop data or an explicit authentication/CAPTCHA blocker.

- [ ] **Step 2: Select ninety distinct products**

Require clear media, usable factual descriptions, broad category coverage, and no unverified third-party trademark authorization. Store source URL, title, bag type, color/material facts actually shown, and media URLs.

- [ ] **Step 3: Download and validate media**

Each product directory must contain at least one readable image or video. Reject corrupt files, duplicate hashes, and images too small for TikTok's picker.

- [ ] **Step 4: Write the product manifest**

The allocation must contain exactly ninety unique product IDs, split into six
disjoint fifteen-product account sets, and retain every selected product's
complete ordered image set.

### Task 4: Generate Six Accurate Caption Sets

**Files:**
- Create: `build/ikunbags-six-account/captions.json`

- [ ] **Step 1: Generate ninety captions**

Create one caption for every product/account assignment. Each caption must use
only that product's facts, contain a useful hook, and avoid unsupported price,
stock, delivery, authenticity, discount, or guarantee claims.

- [ ] **Step 2: Validate caption coverage**

Confirm `15 distinct products × 6 accounts = 90` non-empty captions and ninety
unique product IDs across the matrix.

### Task 5: Publish And Verify Ninety Posts

**Files:**
- Create: `build/ikunbags-six-account/publish-results.jsonl`
- Create: `build/ikunbags-six-account/evidence/<device-id>/`

- [ ] **Step 1: Establish each account's profile-grid baseline**

Capture the visible post count/grid before publishing. Do not publish if the visible username differs from Task 1.

- [ ] **Step 2: Publish one product at a time per account**

Use visible TikTok UI to select all ordered images belonging to exactly one
staged product, enter the assigned caption, and publish. Do not combine images
from different products. Do not enable a story, paid disclosure, location, Shop
attachment, or other optional setting unless already required by a verified listing.

- [ ] **Step 3: Verify each post before advancing**

Return to the same account profile and require a new visible grid item or count change. Append `confirmed`, `failed`, or `uncertain` with screenshot path to `publish-results.jsonl`.

- [ ] **Step 4: Stop safely on blockers**

Stop that account on CAPTCHA, restriction, identity mismatch, uncertain publish, or repeated picker/upload failure. Continue independent devices only when their identities and states remain verified.

### Task 6: Final Acceptance

**Files:**
- Create: `build/ikunbags-six-account/final-report.md`

- [ ] **Step 1: Re-open all six profiles**

Capture final visible avatar, nickname, username, and grid for every available account.

- [ ] **Step 2: Reconcile results**

Count confirmed posts per device from `publish-results.jsonl`. Never count attempted or uncertain publishes as complete.

- [ ] **Step 3: Write the final report**

Report profile status and `confirmed/15` posts per account, every blocker, any nickname cooldown date, and explicit confirmation that VMOS device/account mappings did not collide.
