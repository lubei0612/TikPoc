from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Callable, Mapping
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_WXT_KEY = b"wxtdefgabcdawn12"
_PRICE_ONLY = re.compile(
    r"^\s*(?:[pP]\s*\d+|[￥¥💰🅿️]\s*\d+)(?:\s*(?:配|双)?(?:盒|礼盒|飞机盒|折叠盒|包装).*)?$"
)
_PRICE_TOKEN = re.compile(r"(?:[￥¥💰]\s*\d+(?:\.\d+)?|\b[pP]\s*\d{2,5}\b)")
_CONTACT = re.compile(
    r"(?:微信|威信|wechat|whatsapp|telegram|手机号|qq|vx|wx|(?:电话|手机)\s*[:：]?\s*\+?\d|\+?\d[\d\s-]{7,}\d)",
    re.IGNORECASE,
)
_SUPPLIER_ONLY = re.compile(
    r"(?:代发|包邮|工厂内部号|一手工厂|接直播|可退换|不拆|批发|拿货|加我|联系商家|均价\s*\d+|库存充足|钢五金|\d+\s*芯片|高级货)",
    re.IGNORECASE,
)
_UNSUPPORTED_CLAIMS = re.compile(
    r"(?:原单|高版本|镜像(?:级)?|顶级复刻|一比一|品质堪比|限量发售|明星同款|必入|抢空)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CatalogProduct:
    source_key: str
    source_id: str
    shop_id: str
    title: str
    description: str
    created_time: int | None
    image_urls: tuple[str, ...]


@dataclass(frozen=True)
class RawCatalogProduct:
    source_key: str
    source_id: str
    shop_id: str
    title: str
    description: str
    price: object
    created_time: int | None
    updated_time: int | None
    labels: tuple[object, ...]
    properties: object
    image_urls: tuple[str, ...]
    video_urls: tuple[str, ...]


CatalogTransport = Callable[[str, str, dict[str, str]], str]


def encrypt_wxt_payload(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_WXT_KEY), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def decrypt_wxt_payload(payload: str) -> dict[str, object]:
    decryptor = Cipher(algorithms.AES(_WXT_KEY), modes.ECB()).decryptor()
    padded = decryptor.update(base64.b64decode(payload)) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    raw = unpadder.update(padded) + unpadder.finalize()
    decoded = json.loads(raw.decode())
    if not isinstance(decoded, dict):
        raise ValueError("WXT payload must decode to an object")
    return decoded


def sanitize_catalog_description(value: object) -> str:
    text = str(value or "").replace("\\n", "\n").replace("\r", "\n")
    clean: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip(" ·,，;；")
        if not line or _PRICE_ONLY.fullmatch(line):
            continue
        if _CONTACT.search(line) or _SUPPLIER_ONLY.search(line):
            continue
        line = _PRICE_TOKEN.sub("", line)
        line = _UNSUPPORTED_CLAIMS.sub("", line)
        line = " ".join(line.split()).strip(" ·,，;；!！")
        if line:
            clean.append(line)
    return "\n".join(clean)


class GxhyCatalogClient:
    def __init__(self, *, transport: CatalogTransport | None = None) -> None:
        self.transport = transport or _post_wxt

    def fetch_page(
        self,
        *,
        shop_id: str,
        market_code: str,
        page_index: int,
        page_size: int = 50,
    ) -> tuple[CatalogProduct, ...]:
        rows = self._fetch_rows(
            shop_id=shop_id,
            market_code=market_code,
            page_index=page_index,
            page_size=page_size,
        )
        return tuple(
            product
            for row in rows
            if (product := _parse_product(row, shop_id=shop_id)) is not None
        )

    def fetch_raw_page(
        self,
        *,
        shop_id: str,
        market_code: str,
        page_index: int,
        page_size: int = 50,
    ) -> tuple[RawCatalogProduct, ...]:
        rows = self._fetch_rows(
            shop_id=shop_id,
            market_code=market_code,
            page_index=page_index,
            page_size=page_size,
        )
        return tuple(
            product
            for row in rows
            if (product := _parse_raw_product(row, shop_id=shop_id)) is not None
        )

    def iter_raw_products(
        self,
        *,
        shop_id: str,
        market_code: str,
        page_size: int = 50,
        max_products: int | None = None,
        delay_seconds: float = 0,
    ) -> Iterator[RawCatalogProduct]:
        if max_products is not None and max_products <= 0:
            raise ValueError("max_products must be positive")
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        emitted = 0
        page_index = 0
        while max_products is None or emitted < max_products:
            rows = self._fetch_rows(
                shop_id=shop_id,
                market_code=market_code,
                page_index=page_index,
                page_size=page_size,
            )
            for row in rows:
                product = _parse_raw_product(row, shop_id=shop_id)
                if product is None:
                    continue
                yield product
                emitted += 1
                if max_products is not None and emitted >= max_products:
                    return
            if len(rows) < page_size:
                return
            page_index += 1
            if delay_seconds:
                time.sleep(delay_seconds)

    def _fetch_rows(
        self,
        *,
        shop_id: str,
        market_code: str,
        page_index: int,
        page_size: int,
    ) -> tuple[Mapping[str, object], ...]:
        if not shop_id.strip():
            raise ValueError("shop_id is required")
        if page_index < 0:
            raise ValueError("page_index must be non-negative")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if not market_code.strip():
            raise ValueError("market_code is required")

        query = urlencode({"marketCode": market_code})
        url = (
            f"https://gxhy1688.com/personProduct/getPersonProductByType.action?{query}"
        )
        request_payload = {
            "keyWord": "",
            "pageIndex": page_index,
            "pageSize": page_size,
            "uid": shop_id,
            "labels": [],
            "type": 0,
            "returnCount": 1,
        }
        response = decrypt_wxt_payload(
            self.transport(
                url,
                encrypt_wxt_payload(request_payload),
                {"Content-Type": "application/wxt;charset=UTF-8"},
            )
        )
        if response.get("success") is not True:
            raise RuntimeError(str(response.get("msg") or "catalog request failed"))
        rows = response.get("data")
        if not isinstance(rows, list):
            raise ValueError("catalog response data must be a list")
        return tuple(row for row in rows if isinstance(row, dict))


def _parse_product(row: Mapping[str, object], *, shop_id: str) -> CatalogProduct | None:
    source_id = str(row.get("id") or row.get("productId") or "").strip()
    if not source_id:
        return None
    pics = row.get("pics")
    pic_list = pics.get("picList") if isinstance(pics, dict) else []
    image_urls = tuple(
        _https_url(value)
        for value in pic_list or []
        if isinstance(value, str) and value.startswith(("http://", "https://"))
    )
    image_urls = tuple(dict.fromkeys(image_urls))
    if not image_urls:
        return None
    description = sanitize_catalog_description(
        row.get("description") or row.get("title")
    )
    if not description:
        return None
    title = sanitize_catalog_description(row.get("title"))
    created = row.get("createdTime")
    return CatalogProduct(
        source_key=f"gxhy:{shop_id}:{source_id}",
        source_id=source_id,
        shop_id=shop_id,
        title=(title.splitlines()[0] if title else description.splitlines()[0]),
        description=description,
        created_time=int(created) if isinstance(created, (int, float)) else None,
        image_urls=image_urls,
    )


def _parse_raw_product(
    row: Mapping[str, object], *, shop_id: str
) -> RawCatalogProduct | None:
    source_id = str(row.get("id") or row.get("productId") or "").strip()
    if not source_id:
        return None
    pics = row.get("pics")
    pic_data = pics if isinstance(pics, dict) else {}
    image_urls = _media_urls(pic_data.get("picList"))
    video_urls = _media_urls(pic_data.get("videoList"))
    labels = row.get("labels")
    label_values = tuple(labels) if isinstance(labels, list) else ()
    return RawCatalogProduct(
        source_key=f"gxhy:{shop_id}:{source_id}",
        source_id=source_id,
        shop_id=shop_id,
        title=str(row.get("title") or "").strip(),
        description=str(row.get("description") or row.get("title") or "").strip(),
        price=row.get("price"),
        created_time=_optional_int(row.get("createdTime")),
        updated_time=_optional_int(row.get("lastEditDate")),
        labels=label_values,
        properties=row.get("props") if row.get("props") is not None else {},
        image_urls=image_urls,
        video_urls=video_urls,
    )


def parse_gxhy_shop(value: str, *, default_market_code: str = "gz") -> tuple[str, str]:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("shop is required")
    if "://" not in candidate:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
            raise ValueError("shop must be a GXHY shop URL or uid")
        return candidate, default_market_code
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname not in {
        "gxhy1688.com",
        "www.gxhy1688.com",
    }:
        raise ValueError("shop URL must use https://gxhy1688.com")
    query = parse_qs(parsed.query)
    shop_id = str((query.get("uid") or [""])[0]).strip()
    market_code = str((query.get("marketCode") or [default_market_code])[0]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", shop_id):
        raise ValueError("shop URL is missing a valid uid")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", market_code):
        raise ValueError("shop URL has an invalid marketCode")
    return shop_id, market_code


def _media_urls(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        dict.fromkeys(
            _https_url(item)
            for item in value
            if isinstance(item, str) and item.startswith(("http://", "https://"))
        )
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _https_url(value: str) -> str:
    if value.startswith("http://"):
        return "https://" + value.removeprefix("http://")
    return value


def _post_wxt(url: str, body: str, headers: dict[str, str]) -> str:
    request = Request(
        url,
        data=body.encode(),
        headers={**headers, "User-Agent": "TikPoc/0.1"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS origin
        return response.read().decode()
