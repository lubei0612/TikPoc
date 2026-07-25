from __future__ import annotations

import json

import pytest

from tikpoc.catalog import (
    GxhyCatalogClient,
    decrypt_wxt_payload,
    encrypt_wxt_payload,
    parse_gxhy_shop,
    sanitize_catalog_description,
)


def test_wxt_payload_round_trip() -> None:
    payload = {"pageIndex": 0, "pageSize": 2, "uid": "shop-01", "labels": []}

    encrypted = encrypt_wxt_payload(payload)

    assert encrypted != json.dumps(payload)
    assert decrypt_wxt_payload(encrypted) == payload


def test_catalog_description_removes_price_and_supplier_metadata() -> None:
    source = """P205 配盒
微信 xcxd88899 电话 15542749760
包邮无痕代发，原单，一手工厂内部号
Soft structured shoulder bag with an adjustable strap.
尺寸：27*12cm
￥205"""

    assert sanitize_catalog_description(source) == (
        "配盒\nSoft structured shoulder bag with an adjustable strap.\n尺寸：27*12cm"
    )


def test_catalog_description_drops_inventory_and_price_code_only_copy() -> None:
    source = "L家全品类接直播‼️库存充足\\n均价270‼️钢五金‼️163芯片‼️高级货‼️"

    assert sanitize_catalog_description(source) == ""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("💰205 配盒", "配盒"),
        ("💰260 配折叠盒飞机盒 配小镜子", "配折叠盒飞机盒 配小镜子"),
        ("大号💰220 配盒 尺寸27*12cm", "大号 配盒 尺寸27*12cm"),
        ("￥205", ""),
    ],
)
def test_catalog_description_removes_price_but_keeps_packaging(
    source: str, expected: str
) -> None:
    assert sanitize_catalog_description(source) == expected


def test_catalog_description_keeps_phone_as_product_capacity_fact() -> None:
    source = "内部空间合理，手机、卡包、钥匙都能装下"

    assert sanitize_catalog_description(source) == source


def test_catalog_client_fetches_bounded_sanitized_products() -> None:
    seen: dict[str, object] = {}

    def transport(url: str, body: str, headers: dict[str, str]) -> str:
        seen.update(url=url, payload=decrypt_wxt_payload(body), headers=headers)
        return encrypt_wxt_payload(
            {
                "success": True,
                "data": [
                    {
                        "id": "product-01",
                        "title": "P140 boxed shoulder bag",
                        "description": "P140配盒\nCompact shoulder bag\n尺寸：30*20cm",
                        "createdTime": 123456,
                        "price": 140,
                        "pics": {
                            "picList": [
                                "http://product.example/person/shop/product-01/0.jpg",
                                "data:image/png;base64,ignored",
                            ]
                        },
                        "userCode": "private-supplier-code",
                    }
                ],
            }
        )

    client = GxhyCatalogClient(transport=transport)

    products = client.fetch_page(
        shop_id="shop-01", market_code="gz", page_index=2, page_size=10
    )

    assert seen["url"] == (
        "https://gxhy1688.com/personProduct/getPersonProductByType.action?marketCode=gz"
    )
    assert seen["payload"] == {
        "keyWord": "",
        "pageIndex": 2,
        "pageSize": 10,
        "uid": "shop-01",
        "labels": [],
        "type": 0,
        "returnCount": 1,
    }
    assert seen["headers"] == {"Content-Type": "application/wxt;charset=UTF-8"}
    assert len(products) == 1
    assert products[0].source_key == "gxhy:shop-01:product-01"
    assert products[0].description == "配盒\nCompact shoulder bag\n尺寸：30*20cm"
    assert products[0].image_urls == (
        "https://product.example/person/shop/product-01/0.jpg",
    )
    assert not hasattr(products[0], "price")
    assert not hasattr(products[0], "user_code")


def test_catalog_client_rejects_unbounded_page_size() -> None:
    client = GxhyCatalogClient(transport=lambda *_: "")

    try:
        client.fetch_page(
            shop_id="shop-01", market_code="gz", page_index=0, page_size=101
        )
    except ValueError as exc:
        assert "page_size" in str(exc)
    else:
        raise AssertionError("expected bounded page size validation")


def test_catalog_client_preserves_public_raw_product_fields() -> None:
    def transport(_url: str, _body: str, _headers: dict[str, str]) -> str:
        return encrypt_wxt_payload(
            {
                "success": True,
                "data": [
                    {
                        "id": "product-01",
                        "title": "P140 boxed shoulder bag",
                        "description": "Raw supplier copy\n尺寸：30*20cm",
                        "createdTime": 123456,
                        "lastEditDate": 123999,
                        "price": 140,
                        "labels": [{"id": "label-1", "name": "新品"}],
                        "props": {"颜色": "黑色"},
                        "pics": {
                            "picList": ["http://product.example/0.jpg"],
                            "videoList": ["https://product.example/0.mp4"],
                        },
                    }
                ],
            }
        )

    product = GxhyCatalogClient(transport=transport).fetch_raw_page(
        shop_id="shop-01", market_code="gz", page_index=0, page_size=50
    )[0]

    assert product.description == "Raw supplier copy\n尺寸：30*20cm"
    assert product.price == 140
    assert product.labels == ({"id": "label-1", "name": "新品"},)
    assert product.properties == {"颜色": "黑色"}
    assert product.image_urls == ("https://product.example/0.jpg",)
    assert product.video_urls == ("https://product.example/0.mp4",)


def test_catalog_client_iterates_until_a_short_page() -> None:
    seen_pages: list[int] = []

    def transport(_url: str, body: str, _headers: dict[str, str]) -> str:
        payload = decrypt_wxt_payload(body)
        page = int(payload["pageIndex"])
        seen_pages.append(page)
        row_count = 2 if page == 0 else 1
        return encrypt_wxt_payload(
            {
                "success": True,
                "data": [
                    {
                        "id": f"product-{page}-{index}",
                        "title": "Product",
                        "description": "Description",
                        "pics": {"picList": ["https://product.example/0.jpg"]},
                    }
                    for index in range(row_count)
                ],
            }
        )

    products = tuple(
        GxhyCatalogClient(transport=transport).iter_raw_products(
            shop_id="shop-01", market_code="gz", page_size=2
        )
    )

    assert len(products) == 3
    assert seen_pages == [0, 1]


def test_parse_gxhy_shop_accepts_uid_or_shop_url() -> None:
    assert parse_gxhy_shop("shop-01") == ("shop-01", "gz")
    assert parse_gxhy_shop(
        "https://gxhy1688.com/Shopindex?marketCode=sz&uid=shop-02"
    ) == ("shop-02", "sz")
