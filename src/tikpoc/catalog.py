from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_WXT_KEY = b"wxtdefgabcdawn12"
_PRICE_ONLY = re.compile(
    r"^\s*(?:[pP]\s*\d+|[￥¥💰🅿️]\s*\d+)(?:\s*(?:配|双)?(?:盒|礼盒|飞机盒|折叠盒|包装).*)?$"
)
_PRICE_TOKEN = re.compile(r"(?:[￥¥💰]\s*\d+(?:\.\d+)?|\b[pP]\s*\d{2,5}\b)")
_CONTACT = re.compile(
    r"(?:微信|威信|wechat|whatsapp|telegram|电话|手机|手机号|qq|vx|wx|\+?\d[\d\s-]{7,}\d)",
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
        if not shop_id.strip():
            raise ValueError("shop_id is required")
        if page_index < 0:
            raise ValueError("page_index must be non-negative")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

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
        return tuple(
            product
            for row in rows
            if isinstance(row, dict)
            and (product := _parse_product(row, shop_id=shop_id)) is not None
        )


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
