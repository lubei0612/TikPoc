# RoxyBrowser Web Photo Publishing Implementation Plan

**Goal:** Prepare catalog products from the configured GXHY shop and publish reviewed photo posts through the visible TikTok Studio web uploader in the two existing RoxyBrowser profiles.

**Architecture:** A Python catalog client reads the shop's paginated encrypted feed, strips price and supplier metadata, downloads and normalizes selected images, and writes account-scoped durable publishing jobs. A RoxyBrowser adapter uses the local OpenAPI only to open/identify profiles and obtain CDP endpoints; Selenium performs visible TikTok Studio actions and reconciles the resulting profile post before completing a job.

**Safety and durability:** No cookies or TikTok session tokens are exported. Jobs are immutable after approval, final submission happens once under a lease, ambiguous results become `uncertain`, and the first live run is capped at one post on each of the two controlled IKUN profiles.

---

## Task 1: Catalog Feed Client

- Add AES-compatible GXHY request/response handling with an injected HTTP transport.
- Fetch a bounded page by shop ID, market code, page index, and page size.
- Return only stable product identity, sanitized description, creation order, and HTTPS image URLs.
- Remove price/package codes, supplier contacts, fulfillment instructions, and source-only metadata before persistence.
- Add focused parsing, encryption, pagination, and sanitization tests.

## Task 2: Image and Caption Preparation

- Download only selected product images with size/type limits and SHA-256 deduplication.
- Reject or crop images with visible price/contact overlays when the product remains usable.
- Normalize accepted images for TikTok photo upload without stretching.
- Generate concise factual English product introductions and a profile-contact call to action.
- Add tests for image limits, duplicate hashes, price filtering, and caption constraints.

## Task 3: Durable Publishing Queue

- Add catalog source, asset, publishing job, lease, attempt, and result tables.
- Enforce uniqueness by source product and target account.
- Implement `discovered -> prepared -> approved -> publishing -> published|uncertain` transitions.
- Add CLI commands to sync, preview, approve, list, and reconcile jobs.

## Task 4: RoxyBrowser TikTok Studio Adapter

- Call local RoxyBrowser OpenAPI for health, profile discovery, open, and connection information.
- Attach Selenium to the returned debugger endpoint without reading profile storage.
- Verify the expected visible TikTok username, open `/tiktokstudio/upload`, select `Photos`, and require an image-capable file input.
- Upload prepared files, fill the immutable caption, preserve existing audience defaults, and stop at a review gate unless the job is approved.
- Submit once, then reconcile a new visible profile post; ambiguous outcomes remain `uncertain`.

## Task 5: Operator UI and Controlled Acceptance

- Add a Publishing workspace to the existing dashboard for source sync, product preview, account assignment, approval, job state, and visible result links.
- Run complete Python/frontend regression and lint/format checks.
- Before final visible posting, notify the operator with the two captions, image counts, and target account mapping.
- Publish exactly one reviewed photo post to each existing controlled Roxy profile and verify no duplicate after reload.
- Update `docs/web-engagement-runbook.md` and `AGENTS.md` with redacted acceptance evidence.
