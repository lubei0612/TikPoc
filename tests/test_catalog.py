from __future__ import annotations

import json

from tikpoc.catalog import (
    GxhyCatalogClient,
    decrypt_wxt_payload,
    encrypt_wxt_payload,
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
        "Soft structured shoulder bag with an adjustable strap.\n尺寸：27*12cm"
    )


def test_catalog_description_drops_inventory_and_price_code_only_copy() -> None:
    source = "L家全品类接直播‼️库存充足\\n均价270‼️钢五金‼️163芯片‼️高级货‼️"

    assert sanitize_catalog_description(source) == ""


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
    assert products[0].description == "Compact shoulder bag\n尺寸：30*20cm"
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
