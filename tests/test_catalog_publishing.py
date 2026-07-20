from __future__ import annotations

from io import BytesIO

from PIL import Image

from tikpoc.catalog import CatalogProduct
from tikpoc.catalog_publishing import (
    CatalogCaptionClient,
    CatalogImagePreparer,
    build_caption_prompt,
    fallback_english_caption,
    normalize_english_caption,
)
from tikpoc.runtime_settings import ProviderCredentials


def _image_bytes(color: str, size: tuple[int, int] = (800, 600)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def _product(*urls: str) -> CatalogProduct:
    return CatalogProduct(
        source_key="gxhy:shop-01:product-01",
        source_id="product-01",
        shop_id="shop-01",
        title="Structured shoulder bag",
        description="Structured shoulder bag\nAdjustable strap\n尺寸：30*20cm",
        created_time=123,
        image_urls=urls,
    )


def test_image_preparer_deduplicates_and_preserves_aspect_ratio(tmp_path) -> None:
    red = _image_bytes("red", (800, 600))
    blue = _image_bytes("blue", (600, 900))
    payloads = {
        "https://example/0.png": red,
        "https://example/1.png": red,
        "https://example/2.png": blue,
    }
    preparer = CatalogImagePreparer(downloader=payloads.__getitem__)

    assets = preparer.prepare(_product(*payloads), output_dir=tmp_path, max_images=10)

    assert len(assets) == 2
    assert len({asset.source_sha256 for asset in assets}) == 2
    for asset in assets:
        with Image.open(asset.path) as image:
            assert image.size == (1080, 1440)
            assert image.mode == "RGB"


def test_image_preparer_rejects_oversized_download(tmp_path) -> None:
    preparer = CatalogImagePreparer(
        downloader=lambda _: b"x" * 101, max_download_bytes=100
    )

    assert (
        preparer.prepare(_product("https://example/large.png"), output_dir=tmp_path)
        == ()
    )


def test_caption_prompt_requires_factual_english_and_no_price() -> None:
    prompt = build_caption_prompt(_product("https://example/0.png"))

    assert "English" in prompt
    assert "Do not include prices" in prompt
    assert "profile link or pinned post" in prompt
    assert "Adjustable strap" in prompt


def test_fallback_caption_translates_known_bag_type_and_dimensions() -> None:
    product = CatalogProduct(
        source_key="gxhy:shop-01:product-02",
        source_id="product-02",
        shop_id="shop-01",
        title="手拿包",
        description="灰色老花手拿包\n尺寸：30*20cm",
        created_time=123,
        image_urls=("https://example/0.png",),
    )

    caption = fallback_english_caption(product)

    assert "clutch bag" in caption
    assert "30 × 20 cm" in caption
    assert not any("\u4e00" <= character <= "\u9fff" for character in caption)


def test_caption_normalization_removes_direct_contacts_and_adds_profile_cta() -> None:
    draft = (
        "A compact everyday bag for work and weekends. Price: $39. "
        "WhatsApp +86 15000000000 for details."
    )

    assert normalize_english_caption(draft) == (
        "A compact everyday bag for work and weekends. "
        "Interested in this style? See our profile link or pinned post for details."
    )


def test_caption_client_uses_configured_provider_and_normalizes_result() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self) -> bytes:
            return (
                b'{"choices":[{"message":{"content":"A structured everyday '
                b'bag with an adjustable strap."}}]}'
            )

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.headers["Authorization"]
        seen["timeout"] = timeout
        return Response()

    client = CatalogCaptionClient(
        ProviderCredentials(
            base_url="https://model.example/v1", api_key="secret", model="model-1"
        ),
        opener=opener,
    )

    caption = client.generate(_product("https://example/0.png"))

    assert seen == {
        "url": "https://model.example/v1/chat/completions",
        "authorization": "Bearer secret",
        "timeout": 30,
    }
    assert caption.endswith("See our profile link or pinned post for details.")
