from __future__ import annotations

from pathlib import Path

import pytest

from tikpoc.catalog import CatalogProduct
from tikpoc.publishing_db import PublishingRepository


def _product() -> CatalogProduct:
    return CatalogProduct(
        source_key="gxhy:shop-01:product-01",
        source_id="product-01",
        shop_id="shop-01",
        title="Clutch bag",
        description="Compact clutch bag",
        created_time=123,
        image_urls=("https://example/0.jpg",),
    )


def test_prepared_job_is_account_scoped_and_immutable(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")

    first = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="First caption",
        asset_paths=(tmp_path / "one.jpg",),
        now_ms=100,
    )
    duplicate = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Changed caption",
        asset_paths=(tmp_path / "two.jpg",),
        now_ms=200,
    )
    other_account = repository.prepare_job(
        _product(),
        account_id="account-02",
        caption="Second account caption",
        asset_paths=(tmp_path / "two.jpg",),
        now_ms=200,
    )

    assert duplicate == first
    assert first.caption == "First caption"
    assert first.asset_paths == (str(tmp_path / "one.jpg"),)
    assert other_account.job_id != first.job_id


def test_job_requires_approval_before_claim(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Caption",
        asset_paths=(tmp_path / "one.jpg",),
        now_ms=100,
    )

    assert (
        repository.claim_job(
            account_id="account-01", owner="worker-01", now_ms=200, lease_ms=1_000
        )
        is None
    )

    repository.approve_job(job.job_id, now_ms=300)
    claimed = repository.claim_job(
        account_id="account-01", owner="worker-01", now_ms=400, lease_ms=1_000
    )

    assert claimed is not None
    assert claimed.state == "publishing"
    assert claimed.lease_owner == "worker-01"


def test_unexpired_publish_lease_is_exclusive(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Caption",
        asset_paths=(tmp_path / "one.jpg",),
        now_ms=100,
    )
    repository.approve_job(job.job_id, now_ms=200)
    repository.claim_job(
        account_id="account-01", owner="worker-01", now_ms=300, lease_ms=1_000
    )

    assert (
        repository.claim_job(
            account_id="account-01", owner="worker-02", now_ms=400, lease_ms=1_000
        )
        is None
    )


def test_completion_records_published_result_once(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Caption",
        asset_paths=(tmp_path / "one.jpg",),
        now_ms=100,
    )
    repository.approve_job(job.job_id, now_ms=200)
    repository.claim_job(
        account_id="account-01", owner="worker-01", now_ms=300, lease_ms=1_000
    )

    completed = repository.finish_job(
        job.job_id,
        owner="worker-01",
        result="published",
        visible_post_url="https://www.tiktok.com/@account/video/123",
        now_ms=500,
    )

    assert completed.state == "published"
    assert completed.visible_post_url.endswith("/123")
    with pytest.raises(ValueError, match="publishing"):
        repository.finish_job(
            job.job_id,
            owner="worker-01",
            result="published",
            visible_post_url="https://www.tiktok.com/@account/video/456",
            now_ms=600,
        )


def test_uncertain_result_is_not_claimed_again(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Caption",
        asset_paths=(tmp_path / "one.jpg",),
        now_ms=100,
    )
    repository.approve_job(job.job_id, now_ms=200)
    repository.claim_job(
        account_id="account-01", owner="worker-01", now_ms=300, lease_ms=1_000
    )
    repository.finish_job(
        job.job_id,
        owner="worker-01",
        result="uncertain",
        visible_post_url="",
        now_ms=400,
    )

    assert (
        repository.claim_job(
            account_id="account-01", owner="worker-02", now_ms=2_000, lease_ms=1_000
        )
        is None
    )


def test_job_persists_immutable_asset_hashes(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")

    job = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Caption",
        asset_paths=(tmp_path / "one.jpg", tmp_path / "two.jpg"),
        asset_sha256s=("a" * 64, "b" * 64),
        now_ms=100,
    )

    assert job.asset_sha256s == ("a" * 64, "b" * 64)
    repeated = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="Changed caption",
        asset_paths=(tmp_path / "other.jpg",),
        asset_sha256s=("c" * 64,),
        now_ms=200,
    )
    assert repeated.asset_sha256s == job.asset_sha256s
