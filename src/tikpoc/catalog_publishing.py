from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

from .catalog import CatalogProduct
from .runtime_settings import ProviderCredentials

_DIRECT_CONTACT = re.compile(
    r"(?:whatsapp|telegram|wechat|微信|电话|手机号|qq|\+?\d[\d\s-]{7,}\d)",
    re.IGNORECASE,
)
_PRICE = re.compile(
    r"(?:\bprice\s*[:：]?\s*|[￥¥$€£]\s*)\d+(?:[.,]\d+)?",
    re.IGNORECASE,
)
_UNSUPPORTED = re.compile(
    r"\b(?:authentic|genuine|official|guaranteed|limited edition|celebrity same style)\b",
    re.IGNORECASE,
)
_CTA = "Interested in this style? See our profile link or pinned post for details."
_BAG_TYPES = (
    ("手拿包", "clutch bag"),
    ("盒子包", "box bag"),
    ("笔筒包", "barrel shoulder bag"),
    ("水桶包", "bucket bag"),
    ("托特包", "tote bag"),
    ("腋下包", "shoulder bag"),
    ("胸包", "crossbody bag"),
    ("双肩包", "backpack"),
    ("保龄球包", "bowling bag"),
    ("包", "bag"),
)


@dataclass(frozen=True)
class PreparedCatalogAsset:
    path: Path
    source_url: str
    source_sha256: str
    width: int
    height: int


ImageDownloader = Callable[[str], bytes]
OverlayChecker = Callable[[Image.Image], bool]


class CatalogImagePreparer:
    def __init__(
        self,
        *,
        downloader: ImageDownloader | None = None,
        overlay_checker: OverlayChecker | None = None,
        max_download_bytes: int = 50 * 1024 * 1024,
        output_size: tuple[int, int] = (1080, 1440),
    ) -> None:
        self.downloader = downloader or _download_image
        self.overlay_checker = overlay_checker
        self.max_download_bytes = max_download_bytes
        self.output_size = output_size

    def prepare(
        self,
        product: CatalogProduct,
        *,
        output_dir: Path,
        max_images: int = 10,
    ) -> tuple[PreparedCatalogAsset, ...]:
        if max_images < 1 or max_images > 35:
            raise ValueError("max_images must be between 1 and 35")
        output_dir.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        prepared: list[PreparedCatalogAsset] = []
        for source_url in product.image_urls:
            if len(prepared) >= max_images:
                break
            try:
                content = self.downloader(source_url)
                if not content or len(content) > self.max_download_bytes:
                    continue
                digest = hashlib.sha256(content).hexdigest()
                if digest in seen:
                    continue
                with Image.open(BytesIO(content)) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    if self.overlay_checker and self.overlay_checker(image):
                        continue
                    normalized = _contain_on_canvas(image, self.output_size)
                    path = output_dir / (
                        f"{product.source_id}-{len(prepared) + 1}-{digest[:10]}.jpg"
                    )
                    normalized.save(path, "JPEG", quality=92, optimize=True)
            except (OSError, UnidentifiedImageError, ValueError):
                continue
            seen.add(digest)
            prepared.append(
                PreparedCatalogAsset(
                    path=path,
                    source_url=source_url,
                    source_sha256=digest,
                    width=self.output_size[0],
                    height=self.output_size[1],
                )
            )
        return tuple(prepared)


class CatalogCaptionClient:
    def __init__(
        self,
        provider: ProviderCredentials,
        *,
        opener: Callable = urlopen,
    ) -> None:
        self.provider = provider
        self.opener = opener

    def generate(self, product: CatalogProduct) -> str:
        fallback = fallback_english_caption(product)
        if not (
            self.provider.base_url and self.provider.api_key and self.provider.model
        ):
            return fallback
        payload = {
            "model": self.provider.model,
            "temperature": 0.3,
            "max_tokens": 180,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You write factual English product-introduction captions "
                        "for TikTok photo posts. Return only the caption."
                    ),
                },
                {"role": "user", "content": build_caption_prompt(product)},
            ],
        }
        request = Request(
            f"{self.provider.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.provider.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=30) as response:
                result = json.loads(response.read())
            content = result["choices"][0]["message"]["content"]
        except (OSError, KeyError, IndexError, TypeError, ValueError):
            return fallback
        return normalize_english_caption(str(content))


def fallback_english_caption(product: CatalogProduct) -> str:
    facts = f"{product.title}\n{product.description}"
    bag_type = next(
        (english for chinese, english in _BAG_TYPES if chinese in facts),
        "everyday bag",
    )
    dimension_match = re.search(
        r"(?:尺寸\s*[:：]?\s*)?(\d+(?:\.\d+)?)\s*[*×xX.]\s*(\d+(?:\.\d+)?)\s*(?:cm)?",
        facts,
        re.IGNORECASE,
    )
    dimensions = (
        f" with a {dimension_match.group(1)} × {dimension_match.group(2)} cm profile"
        if dimension_match
        else ""
    )
    style = ""
    if "灰色" in facts:
        style += "gray-toned "
    if "帆布" in facts:
        style += "canvas "
    if "老花" in facts:
        style += "monogram-style "
    capacity = (
        " It has practical room for a phone, cards, and keys."
        if all(value in facts for value in ("手机", "卡包", "钥匙"))
        else ""
    )
    lead = (
        f"A versatile {style}{bag_type}{dimensions}, designed for carrying "
        f"everyday essentials in a compact silhouette.{capacity}"
    )
    return normalize_english_caption(lead)


def build_caption_prompt(product: CatalogProduct) -> str:
    return (
        "Write a concise English TikTok photo-post caption for the product facts "
        "below. Use two or three short sentences and at most three relevant "
        "hashtags. Do not include prices, supplier details, direct contact values, "
        "shipping promises, scarcity, authenticity claims, or claims not stated in "
        "the facts. Describe the item rather than claiming brand authorization. End "
        "with a natural invitation to use the profile link or pinned post for "
        "details.\n\nProduct facts:\n" + product.description[:2_000]
    )


def normalize_english_caption(value: str, *, max_characters: int = 500) -> str:
    text = " ".join(str(value or "").split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    safe: list[str] = []
    for sentence in sentences:
        if _DIRECT_CONTACT.search(sentence) or _PRICE.search(sentence):
            continue
        sentence = _UNSUPPORTED.sub("", sentence)
        sentence = " ".join(sentence.split()).strip(" ,;:-")
        if sentence and _CTA.lower() not in sentence.lower():
            safe.append(sentence)
    base = " ".join(safe).strip()
    available = max(0, max_characters - len(_CTA) - (1 if base else 0))
    if len(base) > available:
        base = base[:available].rsplit(" ", 1)[0].rstrip(" ,;:-")
        if base and base[-1] not in ".!?":
            base += "."
    return f"{base} {_CTA}".strip()


def _contain_on_canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, "white")
    fitted = image.copy()
    fitted.thumbnail(size, Image.Resampling.LANCZOS)
    position = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    canvas.paste(fitted, position)
    return canvas


def _download_image(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "TikPoc/0.1"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - catalog HTTPS URLs
        return response.read(50 * 1024 * 1024 + 1)
