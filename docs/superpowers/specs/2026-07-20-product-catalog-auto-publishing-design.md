# Product Catalog Auto-Publishing Design

**Date:** 2026-07-20

## Objective

Turn products from the configured `gxhy1688.com` shop catalog into English
TikTok product-introduction posts. Each product becomes one photo post, prices
and supplier metadata are removed, publishing is account-scoped and durable,
and the initial live acceptance publishes exactly two controlled posts through
the two existing RoxyBrowser TikTok profiles.

## Initial Acceptance Scope

- Source: the configured shop catalog URL and its paginated product feed.
- Select the latest two product cards that have usable product images and a
  meaningful product description.
- One product becomes one TikTok photo post.
- Publish one controlled post through each existing RoxyBrowser profile.
- Do not act on any additional TikTok account.
- Persist the source identity, image hashes, generated caption, target account,
  publish state, and resulting TikTok post URL or visible post identifier.
- Do not retain supplier contact values, browser credentials, cookies, tokens,
  or unrelated page content.

## Approaches

### RoxyBrowser Web Publishing

Use the current logged-in RoxyBrowser profiles and TikTok's visible web upload
flow. This is available immediately and is selected for the first two posts.
The adapter must verify the visible TikTok username before selecting files or
submitting a post, and must reconcile the resulting visible post before marking
the job complete.

### TikTok Content Posting API

Create a TikTok for Developers application, add the Content Posting API product,
and connect each TikTok account through OAuth with the `video.publish` scope.
Photo URLs must be hosted under a domain or URL prefix verified by the TikTok
developer application. Unreviewed clients produce private-only posts; public
posting is enabled after TikTok approves the application audit.

### Hybrid Publishing

Keep catalog ingestion, image normalization, English caption generation,
scheduling, deduplication, and result persistence independent of the publishing
transport. Start with the RoxyBrowser adapter and later add the official API
adapter without changing catalog or queue records. This is the selected
long-term architecture.

## Components

### Catalog Source Adapter

The adapter loads the shop catalog incrementally, extracts the product image
URLs and visible description, and assigns a stable source key from shop ID plus
image identity. It records a pagination checkpoint so later runs ingest only
new or changed products.

### Image Processor

The processor downloads only the selected product assets, validates image type
and dimensions, computes SHA-256 hashes, and removes duplicate images. The page
price displayed outside the product image is never included. Images containing
embedded price text or supplier contact text are cropped only when the product
remains clearly visible; otherwise the candidate is skipped. Output is sized
for a TikTok photo post without stretching.

### English Product Caption Generator

The caption generator extracts verifiable attributes such as bag type, color,
dimensions, carrying modes, capacity, material when explicitly stated, and use
cases. It removes source price codes, package price markers, supplier contact
details, fulfillment instructions, unsupported scarcity claims, and unsupported
authenticity or quality claims. The output is concise English product-intro
copy followed by one profile-contact call to action.

### Durable Publishing Queue

Each job contains one source product, normalized assets, immutable caption,
assigned TikTok account, publishing transport, and state. A unique constraint
on source product plus target account prevents duplicate posting. States are:

```text
discovered -> prepared -> approved -> publishing -> published
                                      \-> uncertain
                         \-> rejected
```

An `uncertain` result blocks automatic retry until visible reconciliation.

### RoxyBrowser Publisher

The publisher starts or attaches to the configured Roxy profile, verifies the
visible TikTok username, opens the TikTok upload surface, selects the prepared
images, fills the immutable English caption, preserves the account's current
audience defaults unless explicitly configured, submits once, and verifies the
new post on the account profile. The first acceptance run is limited to one job
per controlled account.

### Official API Publisher

The API publisher stores OAuth refresh tokens only in the ignored owner-only
secret store. It queries creator information before publishing, initializes a
photo post using verified public image URLs, polls publish status, and stores
only redacted status evidence. OAuth client secrets and account tokens are not
stored in SQLite, logs, browser extension storage, or committed files.

## Operator Console

Add a Publishing workspace with:

- source synchronization status and last checkpoint;
- product preview with source images and cleaned English caption;
- duplicate, rejected, prepared, publishing, published, and uncertain states;
- target account and transport selection;
- publish-now approval for the initial Roxy pilot;
- later schedule controls with per-account daily limits;
- visible result link and reconciliation status.

The pilot may be executed from the CLI first, but it must write the same queue
records the console will consume.

## Error Handling

- A failed catalog page does not advance the pagination checkpoint.
- Invalid or unreachable images reject only the affected product.
- Empty or unsupported descriptions require a deterministic factual fallback.
- Identity mismatch, signed-out, verification, or ambiguous Roxy profiles stop
  before file selection.
- Navigation or upload failure after submission produces `uncertain`; it does
  not submit again automatically.
- TikTok rejection or visible post disappearance is recorded separately from a
  transport error.

## Testing

Automated tests cover catalog parsing, pagination checkpoints, image hashes,
embedded-price rejection, caption field removal, English output constraints,
account-scoped queue deduplication, immutable jobs, Roxy identity gates,
single-submit behavior, uncertain reconciliation, and API token redaction.

The live pilot requires:

1. exactly two source products;
2. one prepared job per existing controlled Roxy account;
3. no price or supplier metadata in either caption;
4. one visible photo post per account;
5. recorded visible result identity;
6. no duplicate after service, browser, or page reload.

## TikTok API Onboarding

The operator creates a TikTok for Developers organization and application,
enables Content Posting API, configures an HTTPS redirect URI, and supplies the
application client key and client secret through the local write-only settings
surface. Each TikTok account is then opened in its own Roxy profile and completes
the OAuth consent flow. The application stores separate account-scoped OAuth
credentials and never asks the operator to copy account cookies or browser
session tokens.

Before the application audit is approved, API-published test content remains
private. The existing Roxy publisher remains available for the controlled live
pilot and as a fallback transport after visible identity verification.
