# GXHY Catalog CLI

`tikpoc catalog scrape` exports the public product feed for one GXHY shop into
an AI-readable local dataset. It uses the same encrypted product API as the
visible shop page and does not require browser cookies or account credentials.

## Find The Shop URL

Open `https://gxhy1688.com/charts?marketCode=gz`, choose **进入店铺**, and copy
the resulting URL. A supported URL looks like:

```text
https://gxhy1688.com/Shopindex?marketCode=gz&uid=SHOP_UID
```

The `uid` alone is also accepted and defaults to market code `gz`.

## Export A Shop

Start with a bounded sample:

```bash
uv run tikpoc catalog scrape \
  --shop 'https://gxhy1688.com/Shopindex?marketCode=gz&uid=SHOP_UID' \
  --output var/catalog/SHOP_UID \
  --max-products 10
```

Remove `--max-products` to continue until the API returns a short final page.
The default page size is `50`, the maximum is `100`, and the default delay
between full pages is `0.5` seconds. Use `--no-images` for metadata-only export.

Useful controls:

```text
--page-size 50       products requested per API page
--delay 0.5          seconds between full API pages
--max-products 100   stop after this many products
--max-image-mb 25    reject a single image above this size
--no-images          retain remote URLs without downloading files
```

## Output Contract

```text
OUTPUT/
  manifest.jsonl
  summary.json
  products/
    PRODUCT_ID/
      product.json
      description.txt
      images/
        001-URL_HASH.jpg
```

`manifest.jsonl` has one JSON object per product and is the primary input for
AI or batch-processing tools. Each object contains stable source identity,
shop ID, original title and description, public price, timestamps, labels,
properties, image/video URLs, and local asset records with SHA-256, dimensions,
byte size, and download status.

The command is idempotent for the same output directory. Existing deterministic
image files are validated and reused. One bad image is recorded as `failed`
without dropping the product or aborting the rest of the batch. Files are
written atomically so readers do not observe partial JSON or image files.

This raw export is intentionally separate from `CatalogProduct`, which removes
supplier and price metadata before TikTok publishing. AI workflows that need
source-faithful catalog data should read this export; publishing workflows must
continue using the sanitized catalog path.

## Automatic Mobile Publishing

The mobile pipeline creates one immutable job per product. Each job contains
only that product's images, preserves their manifest order, and posts them
together as one TikTok photo post. AI caption generation is best-effort; the
deterministic English caption is used when the configured provider is
unavailable.

Prepare downloaded products without a manual approval gate:

```bash
uv run tikpoc catalog prepare \
  --manifest var/catalog/SHOP_UID/manifest.jsonl \
  --db var/tikpoc.db \
  --account-id account-slot-01-user8362279234711 \
  --output var/catalog/SHOP_UID/publishing
```

Publish one prepared job through the configured slot:

```bash
uv run tikpoc catalog publish \
  --db var/tikpoc.db \
  --devices config/settings.yaml \
  --device-id myt-slot-01 \
  --expected-username user8362279234711 \
  --max-posts 1
```

The end-to-end command combines scrape, prepare, and publish:

```bash
uv run tikpoc catalog run \
  --shop 'https://gxhy1688.com/Shopindex?marketCode=gz&uid=SHOP_UID' \
  --catalog-output var/catalog/SHOP_UID \
  --db var/tikpoc.db \
  --devices config/settings.yaml \
  --device-id myt-slot-01 \
  --expected-username user8362279234711 \
  --max-products 1 \
  --max-posts 1
```

`published` requires a newly visible post on the expected profile. An error
after the Post click is stored as `uncertain` and is never retried
automatically. Identity, verification, or media-selection failures before the
click return the job to `approved`. Inspect durable state with
`tikpoc catalog status --db var/tikpoc.db`.

## Slot 1 Live Acceptance (2026-07-22)

A controlled run on slot 1 completed the pipeline for one TOP1-shop product:

- one immutable product job staged five ordered images in its isolated album;
- TikTok selected all five images and the publisher activated Post exactly once;
- the first automatic reconciliation was obscured by a first-use Profile modal,
  so the durable result correctly froze as `uncertain` without retrying;
- read-only Profile evidence then confirmed the exact expected account, one new
  visible product post, the expected footwear cover, and play count `0`;
- the same job was reconciled from that visible evidence to `published`.

Final durable status was `published=1`, `uncertain=0`. This proves the bounded
one-product, multi-image slot-1 path; it does not authorize or claim an
unattended bulk publishing run.
