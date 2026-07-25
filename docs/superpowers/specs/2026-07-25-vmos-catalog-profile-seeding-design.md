# VMOS Catalog Profile Seeding Design

**Date:** 2026-07-25

## Objective

Seed the TikTok profile `ikunshopp` with 20 distinct, globally relevant luxury
bag photo posts sourced from the configured GXHY shop. Each product becomes one
photo post containing all usable product images in source order. Captions are
rewritten in concise English, prices and supplier-only language are removed,
and explicit product and packaging facts may be retained.

## Approved Publishing Architecture

Use the existing TikPoc catalog and mobile publisher as the source of truth for
selection, sanitization, immutable jobs, idempotency, account identity, and
visible post verification. Use VMOS Cloud as the device transport:

- OpenAPI provides instance discovery, file upload, app start, ADB access, RPA
  dispatch, and task-log queries.
- TikPoc stages one isolated album per product and drives the visible TikTok
  photo-post flow through the connected VMOS instance.
- The VMOS `TikTok发布图集` template is not the primary publisher because the
  visible template currently requires TikTok 41.4.15 in English and does not
  expose TikPoc's product-level sanitization, deduplication, immutable job, and
  post-reconciliation guarantees.
- The VMOS template remains a later canary transport after its input contract
  and visible result semantics are tested against one synthetic job.

This boundary keeps the catalog and queue independent of the cloud-phone
vendor while using VMOS APIs to remove manual file and ADB setup.

## Product Selection

1. Export at least 100 current products from the configured GXHY shop.
2. Research current editorial shopping and trend lists before ranking. Initial
   reference signals include Vogue's 2025 top-shopped handbags and ELLE's 2026
   editor-selected designer bags.
3. Map external demand signals to products actually present in the GXHY
   manifest. A model or recognizable family must exist in both the research set
   and the source catalog to receive a direct trend score.
4. Select 20 distinct model families. Different colors, sizes, or near-identical
   supplier cards do not consume additional slots unless the underlying model is
   materially different.
5. Prefer candidates with a clear model or bag type, complete image set,
   explicit dimensions or carrying modes, and at least five usable images.
6. Store the selection rationale and source URLs beside the immutable job
   manifest. Editorial mentions are evidence of relevance, not a claim of
   guaranteed sales.

## Caption Contract

The English rewrite may retain source-supported:

- brand and model or product-family name;
- bag type, color, dimensions, carrying modes, capacity, and material;
- included box, gift box, pouch, charm, strap, mirror, or other packaging and
  accessories explicitly stated by the source;
- concise styling and use-case language grounded in the source description.

The rewrite removes:

- every price, currency symbol, price tier, and size-to-price mapping;
- supplier handles, contact details, internal seller codes, and fulfillment
  instructions;
- Chinese wholesale calls to action;
- unsupported authenticity, original-grade, scarcity, waterproofing, material,
  celebrity, or quality claims.

Each caption is English-only, concise, immutable after approval, and contains
no invented inventory, discount, delivery, or payment promise.

## Asset Contract

- One GXHY product is one TikTok photo post.
- All valid product images are downloaded in manifest order, content-hashed,
  deduplicated, and staged in a job-scoped album.
- A post contains only that product's assets and between 1 and 35 images.
- Failed, duplicate, corrupt, oversized, or non-image assets are recorded and
  excluded without mixing assets from another product.
- Source image pixels are preserved except for format normalization required by
  the TikTok picker. Price text embedded in source images is not silently
  altered; a candidate whose images materially expose unwanted pricing is
  replaced before approval.

## Execution Flow

1. Scrape and rank candidates; freeze the selected 20 source identities.
2. Download, validate, and stage all image sets.
3. Generate and validate 20 English captions against the caption contract.
4. Resolve the VMOS instance through OpenAPI, verify the expected TikTok package
   and visible username `ikunshopp`, and upload the job-scoped media.
5. Publish one canary job exactly once and reconcile a newly visible post on the
   expected profile.
6. If the canary is confirmed, publish the remaining 19 sequentially. A failure
   before submission returns the job to `approved`; ambiguity after tapping
   Post becomes `uncertain` and blocks automatic resubmission.
7. Persist product identity, asset hashes, caption, account, VMOS instance,
   state, timestamps, and visible result evidence.

## Verification

Automated verification covers price removal without stripping packaging facts,
English-only captions, duplicate model-family exclusion, ordered asset staging,
VMOS API redaction, account identity, single submission, and uncertain-state
idempotency.

Live acceptance requires:

- exactly 20 approved distinct products and no repeated supplier variant;
- one confirmed canary before the remaining queue is released;
- one newly visible photo post per published job on `ikunshopp`;
- no price in any caption and no cross-product image mixing;
- no duplicate submission after interruption or restart;
- a final report separating confirmed, approved, failed, and uncertain jobs.

## Recorded VMOS Touch Baseline

The 2026-07-25 single-device diagnostic completed 50 of 50 target visits. Its
raw measured pace was about 287 visits per hour, or about 12.5 seconds per
target. Nineteen historical action results remained uncertain from before the
TikTok 46 action-state compatibility fixes, so this interrupted diagnostic is
not a clean production-capacity result. The next capacity decision requires an
uninterrupted one-hour canary.
