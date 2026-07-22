from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tikpoc.catalog_workflow import (
    CatalogSupervisor,
    load_catalog_manifest,
    prepare_catalog_jobs,
)
from tikpoc.publishing_db import PublishingRepository
from tikpoc.runtime_settings import ProviderCredentials


def _write_catalog(root: Path, *, source_id: str = "product-01") -> Path:
    image_dir = root / "products" / source_id / "images"
    image_dir.mkdir(parents=True)
    assets = []
    urls = []
    for index, content in enumerate((b"front", b"side", b"detail"), start=1):
        image = image_dir / f"{index:03d}.jpg"
        image.write_bytes(content)
        url = f"https://images.example/{index}.jpg"
        urls.append(url)
        assets.append(
            {
                "source_url": url,
                "status": "downloaded",
                "local_path": f"products/{source_id}/images/{index:03d}.jpg",
                "sha256": hashlib.sha256(content).hexdigest(),
                "width": 800,
                "height": 600,
            }
        )
    record = {
        "source_key": f"gxhy:shop-01:{source_id}",
        "source_id": source_id,
        "shop_id": "shop-01",
        "title": "Structured shoulder bag",
        "description": "Structured shoulder bag\nAdjustable strap",
        "created_time": 123,
        "image_urls": urls,
        "assets": assets,
    }
    manifest = root / "manifest.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return manifest


def test_manifest_loader_binds_multiple_assets_to_one_product(tmp_path: Path) -> None:
    candidate = load_catalog_manifest(_write_catalog(tmp_path))[0]

    assert candidate.product.source_key == "gxhy:shop-01:product-01"
    assert len(candidate.assets) == 3
    assert all(asset.path.parent.name == "images" for asset in candidate.assets)
    assert len({asset.sha256 for asset in candidate.assets}) == 3


@pytest.mark.parametrize(
    "local_path",
    ["../other.jpg", "products/product-02/images/001.jpg", "/tmp/outside.jpg"],
)
def test_manifest_loader_rejects_cross_product_or_escaping_assets(
    tmp_path: Path, local_path: str
) -> None:
    manifest = _write_catalog(tmp_path)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["assets"][0]["local_path"] = local_path
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="asset path"):
        load_catalog_manifest(manifest)


def test_manifest_loader_rejects_changed_asset_bytes(tmp_path: Path) -> None:
    manifest = _write_catalog(tmp_path)
    (tmp_path / "products/product-01/images/001.jpg").write_bytes(b"changed")

    with pytest.raises(ValueError, match="SHA-256"):
        load_catalog_manifest(manifest)


def test_supervisor_auto_approves_one_immutable_multi_image_job(tmp_path: Path) -> None:
    candidate = load_catalog_manifest(_write_catalog(tmp_path / "catalog"))[0]
    repository = PublishingRepository(tmp_path / "publishing.db")
    supervisor = CatalogSupervisor(ProviderCredentials("", "", ""))

    jobs = prepare_catalog_jobs(
        (candidate,),
        repository=repository,
        supervisor=supervisor,
        account_ids=("account-01",),
        output_dir=tmp_path / "prepared",
        now_ms=100,
    )

    assert len(jobs) == 1
    assert jobs[0].state == "approved"
    assert len(jobs[0].asset_paths) == 3
    assert jobs[0].asset_sha256s == tuple(asset.sha256 for asset in candidate.assets)
    repeated = prepare_catalog_jobs(
        (candidate,),
        repository=repository,
        supervisor=supervisor,
        account_ids=("account-01",),
        output_dir=tmp_path / "prepared",
        now_ms=200,
    )
    assert repeated[0].job_id == jobs[0].job_id
    assert repeated[0].caption == jobs[0].caption
    assert repeated[0].asset_paths == jobs[0].asset_paths


def test_prepare_catalog_jobs_assigns_products_round_robin(tmp_path: Path) -> None:
    first = load_catalog_manifest(_write_catalog(tmp_path / "one", source_id="p1"))[0]
    second = load_catalog_manifest(_write_catalog(tmp_path / "two", source_id="p2"))[0]
    repository = PublishingRepository(tmp_path / "publishing.db")

    jobs = prepare_catalog_jobs(
        (first, second),
        repository=repository,
        supervisor=CatalogSupervisor(ProviderCredentials("", "", "")),
        account_ids=("account-01", "account-02"),
        output_dir=tmp_path / "prepared",
        now_ms=100,
    )

    assert [(job.source_key, job.account_id) for job in jobs] == [
        (first.product.source_key, "account-01"),
        (second.product.source_key, "account-02"),
    ]
