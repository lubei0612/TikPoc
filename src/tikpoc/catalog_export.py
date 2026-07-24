from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError

from .catalog import RawCatalogProduct


@dataclass(frozen=True)
class CatalogExportResult:
    product_count: int
    image_count: int
    failed_image_count: int


ImageDownloader = Callable[[str, int], bytes]


class CatalogExporter:
    def __init__(self, *, downloader: ImageDownloader | None = None) -> None:
        self.downloader = downloader or _download_image

    def export(
        self,
        products: Iterable[RawCatalogProduct],
        *,
        output_dir: Path,
        download_images: bool = True,
        max_image_bytes: int = 25 * 1024 * 1024,
    ) -> CatalogExportResult:
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be positive")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[dict[str, object]] = []
        image_count = 0
        failed_image_count = 0
        for product in products:
            product_dir = output_dir / "products" / _safe_component(product.source_id)
            image_dir = product_dir / "images"
            product_dir.mkdir(parents=True, exist_ok=True)
            assets: list[dict[str, object]] = []
            for index, url in enumerate(product.image_urls, start=1):
                if not download_images:
                    assets.append({"source_url": url, "status": "remote"})
                    continue
                try:
                    asset = self._download_asset(
                        url,
                        index=index,
                        image_dir=image_dir,
                        output_dir=output_dir,
                        max_image_bytes=max_image_bytes,
                    )
                except (OSError, UnidentifiedImageError, ValueError) as error:
                    assets.append(
                        {
                            "source_url": url,
                            "status": "failed",
                            "error": str(error) or type(error).__name__,
                        }
                    )
                    failed_image_count += 1
                else:
                    assets.append(asset)
                    image_count += 1
            record: dict[str, object] = {
                "source": "gxhy1688",
                "source_key": product.source_key,
                "source_id": product.source_id,
                "shop_id": product.shop_id,
                "title": product.title,
                "description": product.description,
                "price": product.price,
                "created_time": product.created_time,
                "updated_time": product.updated_time,
                "labels": product.labels,
                "properties": product.properties,
                "image_urls": product.image_urls,
                "video_urls": product.video_urls,
                "assets": assets,
            }
            _atomic_write_text(
                product_dir / "product.json",
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            )
            _atomic_write_text(
                product_dir / "description.txt", product.description.rstrip() + "\n"
            )
            manifest.append(record)
        _atomic_write_text(
            output_dir / "manifest.jsonl",
            "".join(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                for record in manifest
            ),
        )
        result = CatalogExportResult(
            product_count=len(manifest),
            image_count=image_count,
            failed_image_count=failed_image_count,
        )
        _atomic_write_text(
            output_dir / "summary.json",
            json.dumps(
                {
                    "product_count": result.product_count,
                    "image_count": result.image_count,
                    "failed_image_count": result.failed_image_count,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return result

    def _download_asset(
        self,
        url: str,
        *,
        index: int,
        image_dir: Path,
        output_dir: Path,
        max_image_bytes: int,
    ) -> dict[str, object]:
        _validate_public_image_url(url)
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        prefix = f"{index:03d}-{url_hash}"
        cached = next(iter(sorted(image_dir.glob(prefix + ".*"))), None)
        if cached is not None:
            content = cached.read_bytes()
            width, height, _suffix = _inspect_image(content)
            path = cached
        else:
            content = self.downloader(url, max_image_bytes)
            if not content:
                raise ValueError("empty image response")
            if len(content) > max_image_bytes:
                raise ValueError("image exceeds configured size limit")
            width, height, suffix = _inspect_image(content)
            image_dir.mkdir(parents=True, exist_ok=True)
            path = image_dir / f"{prefix}{suffix}"
            _atomic_write_bytes(path, content)
        return {
            "source_url": url,
            "status": "downloaded",
            "local_path": path.relative_to(output_dir).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "width": width,
            "height": height,
        }


def _inspect_image(content: bytes) -> tuple[int, int, str]:
    with Image.open(BytesIO(content)) as image:
        image.verify()
        width, height = image.size
        image_format = str(image.format or "").upper()
    suffix = {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
        "GIF": ".gif",
    }.get(image_format)
    if suffix is None:
        raise ValueError(f"unsupported image format: {image_format or 'unknown'}")
    return width, height, suffix


def _download_image(url: str, max_bytes: int) -> bytes:
    request = Request(url, headers={"User-Agent": "TikPoc/0.1"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed source URLs
        content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("image exceeds configured size limit")
    return content


def _validate_public_image_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("image URL host is not public")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("image URL host is not public")


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return normalized[:120] or hashlib.sha256(str(value).encode()).hexdigest()[:20]


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
