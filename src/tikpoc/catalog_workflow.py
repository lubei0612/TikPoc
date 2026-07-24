from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .catalog import CatalogProduct, sanitize_catalog_description
from .catalog_publishing import CatalogCaptionClient
from .publishing_db import PublishingJob, PublishingRepository
from .runtime_settings import ProviderCredentials


@dataclass(frozen=True)
class CatalogAsset:
    path: Path
    source_url: str
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class CatalogCandidate:
    product: CatalogProduct
    assets: tuple[CatalogAsset, ...]


class CatalogSupervisor:
    def __init__(self, provider: ProviderCredentials) -> None:
        self.caption_client = CatalogCaptionClient(provider)

    def caption(self, candidate: CatalogCandidate) -> str:
        if not candidate.assets:
            raise ValueError("catalog candidate has no usable assets")
        return self.caption_client.generate(candidate.product)


def load_catalog_manifest(path: Path) -> tuple[CatalogCandidate, ...]:
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file():
        raise ValueError(f"catalog manifest does not exist: {path}")
    root = manifest_path.parent
    candidates: list[CatalogCandidate] = []
    seen_sources: set[str] = set()
    for line_number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid manifest JSON on line {line_number}") from error
        if not isinstance(record, dict):
            raise ValueError(f"manifest line {line_number} must be an object")
        source_key = str(record.get("source_key") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        shop_id = str(record.get("shop_id") or "").strip()
        if not source_key or not source_id or not shop_id:
            raise ValueError(f"manifest identity is incomplete on line {line_number}")
        if source_key in seen_sources:
            raise ValueError(f"duplicate source_key in manifest: {source_key}")
        seen_sources.add(source_key)
        image_urls = tuple(
            str(value).strip()
            for value in record.get("image_urls") or ()
            if str(value).strip()
        )
        expected_dir = (
            root / "products" / _safe_component(source_id) / "images"
        ).resolve()
        assets: list[CatalogAsset] = []
        seen_hashes: set[str] = set()
        for raw_asset in record.get("assets") or ():
            if (
                not isinstance(raw_asset, dict)
                or raw_asset.get("status") != "downloaded"
            ):
                continue
            relative = Path(str(raw_asset.get("local_path") or ""))
            if relative.is_absolute():
                raise ValueError("asset path must stay inside its product directory")
            candidate_path = (root / relative).resolve()
            try:
                candidate_path.relative_to(expected_dir)
            except ValueError:
                raise ValueError(
                    "asset path must stay inside its product directory"
                ) from None
            if not candidate_path.is_file():
                raise ValueError(f"asset path does not exist: {relative}")
            expected_hash = str(raw_asset.get("sha256") or "").lower()
            observed_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
            if expected_hash != observed_hash:
                raise ValueError(f"asset SHA-256 mismatch: {relative}")
            if observed_hash in seen_hashes:
                continue
            source_url = str(raw_asset.get("source_url") or "").strip()
            if source_url not in image_urls:
                raise ValueError("asset source URL does not belong to the product")
            seen_hashes.add(observed_hash)
            assets.append(
                CatalogAsset(
                    path=candidate_path,
                    source_url=source_url,
                    sha256=observed_hash,
                    width=int(raw_asset.get("width") or 0),
                    height=int(raw_asset.get("height") or 0),
                )
            )
        if not assets:
            continue
        description = sanitize_catalog_description(record.get("description"))
        title = sanitize_catalog_description(record.get("title"))
        if not description:
            description = title
        if not description:
            continue
        candidates.append(
            CatalogCandidate(
                product=CatalogProduct(
                    source_key=source_key,
                    source_id=source_id,
                    shop_id=shop_id,
                    title=(
                        title.splitlines()[0] if title else description.splitlines()[0]
                    ),
                    description=description,
                    created_time=_optional_int(record.get("created_time")),
                    image_urls=image_urls,
                ),
                assets=tuple(assets),
            )
        )
    return tuple(candidates)


def prepare_catalog_jobs(
    candidates: tuple[CatalogCandidate, ...],
    *,
    repository: PublishingRepository,
    supervisor: CatalogSupervisor,
    account_ids: tuple[str, ...],
    output_dir: Path,
    now_ms: int,
) -> tuple[PublishingJob, ...]:
    accounts = tuple(value.strip() for value in account_ids if value.strip())
    if not accounts:
        raise ValueError("at least one target account is required")
    jobs: list[PublishingJob] = []
    for index, candidate in enumerate(candidates):
        account_id = accounts[index % len(accounts)]
        destination = Path(output_dir) / _safe_component(candidate.product.source_key)
        destination.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for ordinal, asset in enumerate(candidate.assets, start=1):
            path = (
                destination
                / f"{ordinal:03d}-{asset.sha256[:12]}{asset.path.suffix.lower()}"
            )
            if not path.exists():
                shutil.copyfile(asset.path, path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
                raise ValueError("prepared asset SHA-256 mismatch")
            paths.append(path)
        job = repository.prepare_job(
            candidate.product,
            account_id=account_id,
            caption=supervisor.caption(candidate),
            asset_paths=tuple(paths),
            asset_sha256s=tuple(asset.sha256 for asset in candidate.assets),
            now_ms=now_ms,
        )
        if job.state == "prepared":
            job = repository.approve_job(job.job_id, now_ms=now_ms)
        jobs.append(job)
    return tuple(jobs)


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return normalized[:120] or hashlib.sha256(str(value).encode()).hexdigest()[:20]


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
