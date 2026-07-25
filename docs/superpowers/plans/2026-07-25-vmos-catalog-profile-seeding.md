# VMOS Catalog Profile Seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select 20 distinct currently relevant bag products from the configured GXHY shop, prepare price-free English multi-image jobs, and publish them idempotently to `ikunshopp` through the VMOS device.

**Architecture:** TikPoc owns catalog ranking, caption sanitization, immutable publishing jobs, and visible TikTok verification. VMOS OpenAPI supplies signed instance/ADB/file/app operations, while the existing Appium mobile publisher remains the verified photo-post transport.

**Tech Stack:** Python 3.14, SQLite, pytest, urllib, HMAC-SHA256, VMOS Cloud OpenAPI, ADB, Appium, TikTok Android UI.

---

## File Structure

- Modify `src/tikpoc/catalog.py`: preserve explicit packaging facts while removing price tokens.
- Create `src/tikpoc/catalog_selection.py`: deterministic trend-signal matching, model-family deduplication, and bounded selection export.
- Create `src/tikpoc/vmos_cloud.py`: VMOS V2 request signing and redacted OpenAPI client.
- Modify `src/tikpoc/cli.py`: expose `catalog select` and VMOS connection controls without logging secrets.
- Create `tests/test_catalog_selection.py`: ranking, distinct-family, and bounded-selection behavior.
- Create `tests/test_vmos_cloud.py`: signing, response validation, instance and ADB parsing, and redaction.
- Modify `tests/test_catalog.py` and `tests/test_cli.py`: packaging sanitization and command contracts.
- Modify `docs/gxhy-catalog-cli.md` and `docs/vmos-cloud-runbook.md`: reproducible selection and VMOS publishing workflow.

### Task 1: Preserve Packaging While Removing Prices

**Files:**
- Modify: `src/tikpoc/catalog.py`
- Test: `tests/test_catalog.py`

- [ ] **Step 1: Write the failing price-and-packaging tests**

```python
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("💰205 配盒", "配盒"),
        ("💰260 配折叠盒飞机盒 配小镜子", "配折叠盒飞机盒 配小镜子"),
        ("大号💰220 配盒 尺寸27*12cm", "大号 配盒 尺寸27*12cm"),
        ("￥205", ""),
    ],
)
def test_catalog_description_removes_price_but_keeps_packaging(source, expected):
    assert sanitize_catalog_description(source) == expected
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_catalog.py -q`

Expected: the current `_PRICE_ONLY` expression drops packaging-only remainder.

- [ ] **Step 3: Remove price tokens before deciding whether a line is empty**

```python
def sanitize_catalog_description(value: object) -> str:
    text = str(value or "").replace("\\n", "\n").replace("\r", "\n")
    clean: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip(" ·,，;；")
        if not line or _CONTACT.search(line) or _SUPPLIER_ONLY.search(line):
            continue
        line = _PRICE_TOKEN.sub("", line)
        line = _UNSUPPORTED_CLAIMS.sub("", line)
        line = " ".join(line.split()).strip(" ·,，;；!！")
        if line:
            clean.append(line)
    return "\n".join(clean)
```

- [ ] **Step 4: Run focused and catalog regression tests**

Run: `uv run pytest tests/test_catalog.py tests/test_catalog_workflow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/catalog.py tests/test_catalog.py
git commit -m "fix: preserve catalog packaging facts"
```

### Task 2: Deterministic 20-Model Trend Selection

**Files:**
- Create: `src/tikpoc/catalog_selection.py`
- Create: `tests/test_catalog_selection.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing selection tests**

```python
def test_select_products_prefers_trend_matches_and_one_source_per_family():
    signals = (
        TrendSignal("chanel-25", ("chanel 25", "25 hobo"), 100, ("https://vogue.example",)),
        TrendSignal("miu-miu-arcadie", ("miu miu arcadie", "arcadie"), 90, ("https://elle.example",)),
    )
    selected = select_trending_products(_manifest_records(), signals, limit=2)
    assert [item.model_family for item in selected] == [
        "chanel-25",
        "miu-miu-arcadie",
    ]
    assert len({item.model_family for item in selected}) == 2


def test_select_products_requires_usable_image_count_and_exact_limit():
    selected = select_trending_products(_manifest_records(), _signals(), limit=2)
    assert len(selected) == 2
    assert all(item.image_count >= 5 for item in selected)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_catalog_selection.py -q`

Expected: import failure because the selection module does not exist.

- [ ] **Step 3: Implement focused immutable selection types and ranking**

```python
@dataclass(frozen=True)
class TrendSignal:
    model_family: str
    aliases: tuple[str, ...]
    weight: int
    source_urls: tuple[str, ...]


@dataclass(frozen=True)
class SelectedCatalogProduct:
    source_key: str
    source_id: str
    model_family: str
    score: int
    image_count: int
    source_urls: tuple[str, ...]


def select_trending_products(records, signals, *, limit: int):
    if limit <= 0:
        raise ValueError("limit must be positive")
    ranked = []
    for record in records:
        searchable = f"{record.get('title', '')} {record.get('description', '')}".lower()
        matches = [s for s in signals if any(alias.lower() in searchable for alias in s.aliases)]
        if not matches or len(record.get("image_urls") or ()) < 5:
            continue
        signal = max(matches, key=lambda item: item.weight)
        ranked.append((signal.weight, str(record["source_key"]), record, signal))
    selected = []
    seen_families = set()
    for score, _, record, signal in sorted(ranked, key=lambda item: (-item[0], item[1])):
        if signal.model_family in seen_families:
            continue
        seen_families.add(signal.model_family)
        selected.append(
            SelectedCatalogProduct(
                source_key=str(record["source_key"]),
                source_id=str(record["source_id"]),
                model_family=signal.model_family,
                score=score,
                image_count=len(record.get("image_urls") or ()),
                source_urls=signal.source_urls,
            )
        )
        if len(selected) == limit:
            break
    if len(selected) != limit:
        raise ValueError(f"only {len(selected)} distinct eligible model families found")
    return tuple(selected)
```

- [ ] **Step 4: Add `catalog select` CLI arguments and JSONL output**

```python
catalog_select = catalog_commands.add_parser("select")
catalog_select.add_argument("--manifest", type=Path, required=True)
catalog_select.add_argument("--signals", type=Path, required=True)
catalog_select.add_argument("--output", type=Path, required=True)
catalog_select.add_argument("--limit", type=int, default=20)
```

The command writes selected source records, model family, score, and research
URLs atomically. It does not download or publish anything.

- [ ] **Step 5: Run selection and CLI tests**

Run: `uv run pytest tests/test_catalog_selection.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tikpoc/catalog_selection.py tests/test_catalog_selection.py src/tikpoc/cli.py tests/test_cli.py
git commit -m "feat: select distinct trending catalog products"
```

### Task 3: VMOS OpenAPI Transport

**Files:**
- Create: `src/tikpoc/vmos_cloud.py`
- Create: `tests/test_vmos_cloud.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing deterministic-signing and parsing tests**

```python
def test_vmos_signer_emits_required_headers_without_secret():
    request = sign_vmos_request(
        access_key="ACCESS",
        secret_key="SECRET",
        host="api.vmoscloud.com",
        path="/vcpcloud/api/padApi/infos",
        body=b'{"page":1,"rows":10}',
        x_date="20260725T010203Z",
    )
    assert request.headers["x-date"] == "20260725T010203Z"
    assert request.headers["x-host"] == "api.vmoscloud.com"
    assert request.headers["x-content-sha256"] == hashlib.sha256(request.body).hexdigest()
    assert "Credential=ACCESS" in request.headers["authorization"]
    assert "SECRET" not in repr(request)


def test_vmos_client_parses_one_instance_and_adb_lease():
    client = VmosCloudClient(_credentials(), transport=_fixture_transport)
    instance = client.list_instances()[0]
    lease = client.open_adb(instance.pad_code, expire_minutes=120)
    assert instance.pad_code == "ACP250625501MXP"
    assert lease.command.startswith("ssh ")
    assert lease.adb_endpoint.startswith("localhost:")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_vmos_cloud.py -q`

Expected: module import failure.

- [ ] **Step 3: Implement V2 HMAC transport and bounded client methods**

```python
@dataclass(frozen=True, repr=False)
class VmosCredentials:
    access_key: str
    secret_key: str


class VmosCloudClient:
    def list_instances(self) -> tuple[VmosInstance, ...]:
        data = self._post("/vcpcloud/api/padApi/infos", {"page": 1, "rows": 100})
        return tuple(_parse_instance(row) for row in data.get("rows", ()))

    def open_adb(self, pad_code: str, *, expire_minutes: int) -> VmosAdbLease:
        data = self._post(
            "/vcpcloud/api/padApi/adb",
            {"padCode": pad_code, "enable": True, "expireMinutes": expire_minutes},
        )
        return _parse_adb_lease(data)

    def start_app(self, pad_code: str, package: str) -> str:
        data = self._post(
            "/vcpcloud/api/padApi/startApp",
            {"pkgName": package, "padCodes": [pad_code]},
        )
        return str(data[0]["taskId"])
```

Validate `code == 200`, cap instance rows at 100, use injected transports in
tests, redact credentials and connection keys from repr/log output, and load
credentials only from an ignored owner-readable env file.

- [ ] **Step 4: Add a read-only `vmos inspect` CLI command**

```python
vmos_inspect = vmos_commands.add_parser("inspect")
vmos_inspect.add_argument("--env-file", type=Path, required=True)
vmos_inspect.add_argument("--pad-code", default="")
```

The command prints pad code, online state, model, and installed TikTok package
only. It never prints API secrets, ADB connection keys, or proxy credentials.

- [ ] **Step 5: Run focused tests and commit**

Run: `uv run pytest tests/test_vmos_cloud.py tests/test_cli.py -q`

```bash
git add src/tikpoc/vmos_cloud.py tests/test_vmos_cloud.py src/tikpoc/cli.py tests/test_cli.py
git commit -m "feat: add VMOS OpenAPI transport"
```

### Task 4: Freeze and Prepare the 20-Product Queue

**Files:**
- Create ignored runtime artifacts under `var/catalog/0cc703016c964d21b1aed580e59b2247/`
- Modify: `docs/gxhy-catalog-cli.md`
- Test: existing catalog suites

- [ ] **Step 1: Export the current shop with images**

```bash
uv run tikpoc catalog scrape \
  --shop 'https://gxhy1688.com/Shopindex?marketCode=gz&uid=0cc703016c964d21b1aed580e59b2247' \
  --output var/catalog/0cc703016c964d21b1aed580e59b2247/source \
  --max-products 500 --page-size 100 --delay 0.5
```

- [ ] **Step 2: Store current editorial trend signals**

Write ignored `trend-signals.json` with normalized model families, aliases,
weights, and direct research URLs. Include only model families that are
supported by current editorial shopping sources and can be matched to this
shop's source descriptions.

- [ ] **Step 3: Freeze exactly 20 distinct products**

```bash
uv run tikpoc catalog select \
  --manifest var/catalog/0cc703016c964d21b1aed580e59b2247/source/manifest.jsonl \
  --signals var/catalog/0cc703016c964d21b1aed580e59b2247/trend-signals.json \
  --output var/catalog/0cc703016c964d21b1aed580e59b2247/selected.jsonl \
  --limit 20
```

Verify 20 source keys, 20 model families, at least five images per product, no
caption price tokens, and no duplicate asset hash inside a product.

- [ ] **Step 4: Prepare immutable jobs for `ikunshopp`**

```bash
uv run tikpoc catalog prepare \
  --manifest var/catalog/0cc703016c964d21b1aed580e59b2247/selected.jsonl \
  --db var/vmos-catalog-ikunshopp.db \
  --account-id ikunshopp \
  --output var/catalog/0cc703016c964d21b1aed580e59b2247/publishing
```

- [ ] **Step 5: Run catalog regression and commit runbook updates**

Run: `uv run pytest tests/test_catalog.py tests/test_catalog_export.py tests/test_catalog_selection.py tests/test_catalog_workflow.py tests/test_publishing_db.py -q`

```bash
git add docs/gxhy-catalog-cli.md docs/vmos-cloud-runbook.md
git commit -m "docs: add VMOS catalog seeding workflow"
```

### Task 5: Publish Canary, Then Remaining 19

**Files:**
- Runtime only: ignored VMOS env, fleet configuration, SQLite DB, screenshots, and logs
- Modify checkpoint section in `docs/gxhy-catalog-cli.md` after acceptance

- [ ] **Step 1: Verify VMOS and TikTok identity before media transfer**

```bash
uv run tikpoc vmos inspect \
  --env-file config/secrets/vmos.env \
  --pad-code ACP250625501MXP
adb -s 127.0.0.1:57203 shell dumpsys package com.zhiliaoapp.musically | grep versionName
```

Require online VMOS, healthy proxy, expected TikTok package, and visible
username `ikunshopp`.

- [ ] **Step 2: Publish exactly one canary**

```bash
uv run tikpoc catalog publish \
  --db var/vmos-catalog-ikunshopp.db \
  --devices config/secrets/vmos-single.yaml \
  --device-id vmos-acp-01 \
  --expected-username ikunshopp \
  --max-posts 1
```

- [ ] **Step 3: Reconcile the canary from visible profile evidence**

Run: `uv run tikpoc catalog status --db var/vmos-catalog-ikunshopp.db --account-id ikunshopp`

Expected: `published=1 uncertain=0`. Confirm the new visible profile post uses
only the canary product's ordered image set and its immutable price-free English
caption.

- [ ] **Step 4: Release the remaining queue only after canary confirmation**

```bash
uv run tikpoc catalog publish \
  --db var/vmos-catalog-ikunshopp.db \
  --devices config/secrets/vmos-single.yaml \
  --device-id vmos-acp-01 \
  --expected-username ikunshopp \
  --max-posts 19
```

Stop on `uncertain`; inspect visible state before any resubmission.

- [ ] **Step 5: Run final verification**

```bash
uv run tikpoc catalog status --db var/vmos-catalog-ikunshopp.db --account-id ikunshopp
uv run pytest -q
uv tool run ruff check --select F,I,UP src tests
uv tool run ruff format --check src tests
git diff --check
```

Expected runtime result: `published=20 uncertain=0`, with 20 distinct source
keys and 20 visible posts on `ikunshopp`. Report any different final counts
without converting uncertain jobs into success.

- [ ] **Step 6: Record live acceptance and commit**

```bash
git add docs/gxhy-catalog-cli.md
git commit -m "docs: record VMOS catalog seeding acceptance"
git push origin feat/web-lead-conversion
```
