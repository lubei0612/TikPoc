import json

import pytest

from tikpoc.catalog_selection import (
    TrendSignal,
    load_trend_signals,
    select_trending_products,
    write_selected_manifest,
)


def _record(source_id: str, description: str, *, images: int = 6):
    return {
        "source_key": f"gxhy:shop:{source_id}",
        "source_id": source_id,
        "title": description,
        "description": description,
        "image_urls": [
            f"https://images.example/{source_id}/{i}.jpg" for i in range(images)
        ],
        "assets": [],
    }


def _signals() -> tuple[TrendSignal, ...]:
    return (
        TrendSignal(
            "chanel-25", ("chanel 25", "25 hobo"), 100, ("https://vogue.example",)
        ),
        TrendSignal(
            "miu-miu-arcadie",
            ("miu miu arcadie", "arcadie"),
            90,
            ("https://elle.example",),
        ),
    )


def test_select_products_prefers_trend_matches_and_one_source_per_family() -> None:
    records = (
        _record("chanel-black", "Chanel 25 hobo black"),
        _record("chanel-white", "Chanel 25 hobo white"),
        _record("arcadie", "Miu Miu Arcadie bowling bag"),
    )

    selected = select_trending_products(records, _signals(), limit=2)

    assert [item.model_family for item in selected] == [
        "chanel-25",
        "miu-miu-arcadie",
    ]
    assert [item.source_id for item in selected] == ["chanel-black", "arcadie"]


def test_select_products_requires_usable_images_and_exact_limit() -> None:
    records = (
        _record("too-few", "Chanel 25 hobo", images=4),
        _record("arcadie", "Miu Miu Arcadie", images=6),
    )

    with pytest.raises(ValueError, match="only 1 distinct"):
        select_trending_products(records, _signals(), limit=2)


def test_trend_signals_and_selected_manifest_round_trip(tmp_path) -> None:
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(
        json.dumps(
            [
                {
                    "model_family": "chanel-25",
                    "aliases": ["Chanel 25"],
                    "weight": 100,
                    "source_urls": ["https://vogue.example"],
                }
            ]
        ),
        encoding="utf-8",
    )
    records = (_record("chanel", "Chanel 25 hobo"),)
    selected = select_trending_products(
        records, load_trend_signals(signals_path), limit=1
    )
    output = tmp_path / "selected.jsonl"

    write_selected_manifest(output, records, selected)

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["source_id"] == "chanel"
    assert written["selection"]["model_family"] == "chanel-25"
    assert written["selection"]["source_urls"] == ["https://vogue.example"]
