from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from tikpoc.catalog import RawCatalogProduct
from tikpoc.catalog_export import CatalogExporter


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), "red").save(output, "JPEG")
    return output.getvalue()


def _product() -> RawCatalogProduct:
    return RawCatalogProduct(
        source_key="gxhy:shop-01:product-01",
        source_id="product-01",
        shop_id="shop-01",
        title="Raw product title",
        description="Line one\nLine two",
        price=140,
        created_time=123,
        updated_time=456,
        labels=({"id": "label-1", "name": "新品"},),
        properties={"颜色": "黑色"},
        image_urls=("https://product.example/0.jpg",),
        video_urls=(),
    )


def test_catalog_export_writes_ai_manifest_and_reuses_downloaded_images(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def downloader(url: str, _max_bytes: int) -> bytes:
        calls.append(url)
        return _jpeg()

    exporter = CatalogExporter(downloader=downloader)

    first = exporter.export((_product(),), output_dir=tmp_path)
    second = exporter.export((_product(),), output_dir=tmp_path)

    assert first.product_count == second.product_count == 1
    assert first.image_count == second.image_count == 1
    assert calls == ["https://product.example/0.jpg"]
    product_dir = tmp_path / "products" / "product-01"
    assert (product_dir / "description.txt").read_text(encoding="utf-8") == (
        "Line one\nLine two\n"
    )
    product = json.loads((product_dir / "product.json").read_text(encoding="utf-8"))
    assert product["title"] == "Raw product title"
    assert product["price"] == 140
    assert product["assets"][0]["sha256"]
    assert (tmp_path / product["assets"][0]["local_path"]).is_file()
    manifest = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert manifest == [product]


def test_catalog_export_records_one_bad_image_without_dropping_product(
    tmp_path: Path,
) -> None:
    exporter = CatalogExporter(downloader=lambda *_: b"not-an-image")

    result = exporter.export((_product(),), output_dir=tmp_path)

    assert result.product_count == 1
    assert result.image_count == 0
    assert result.failed_image_count == 1
    product = json.loads(
        (tmp_path / "products/product-01/product.json").read_text(encoding="utf-8")
    )
    assert product["assets"][0]["status"] == "failed"
    assert "error" in product["assets"][0]


def test_catalog_export_rejects_private_image_urls_without_downloading(
    tmp_path: Path,
) -> None:
    product = _product()
    product = RawCatalogProduct(
        **{
            **product.__dict__,
            "image_urls": ("https://127.0.0.1/private.jpg",),
        }
    )

    exporter = CatalogExporter(
        downloader=lambda *_: (_ for _ in ()).throw(
            AssertionError("private URL must not be downloaded")
        )
    )
    result = exporter.export((product,), output_dir=tmp_path)

    assert result.failed_image_count == 1
    record = json.loads(
        (tmp_path / "products/product-01/product.json").read_text(encoding="utf-8")
    )
    assert record["assets"][0]["error"] == "image URL host is not public"
