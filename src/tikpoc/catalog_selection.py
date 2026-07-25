from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrendSignal:
    model_family: str
    aliases: tuple[str, ...]
    weight: int
    source_urls: tuple[str, ...]


@dataclass(frozen=True)
class SelectedCatalogProduct:
    source_key: str
    source_id: str
    model_family: str
    score: int
    image_count: int
    source_urls: tuple[str, ...]


def load_manifest_records(path: Path) -> tuple[dict[str, object], ...]:
    records = []
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        if not isinstance(value, dict):
            raise ValueError(f"manifest line {line_number} must be an object")
        records.append(value)
    return tuple(records)


def load_trend_signals(path: Path) -> tuple[TrendSignal, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("trend signals must be a JSON array")
    signals = []
    seen = set()
    for index, value in enumerate(raw, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"trend signal {index} must be an object")
        family = str(value.get("model_family") or "").strip()
        aliases = tuple(
            str(alias).strip()
            for alias in value.get("aliases") or ()
            if str(alias).strip()
        )
        urls = tuple(
            str(url).strip()
            for url in value.get("source_urls") or ()
            if str(url).strip()
        )
        weight = int(value.get("weight") or 0)
        if not family or family in seen or not aliases or weight <= 0 or not urls:
            raise ValueError(f"trend signal {index} is incomplete or duplicated")
        seen.add(family)
        signals.append(TrendSignal(family, aliases, weight, urls))
    return tuple(signals)


def select_trending_products(
    records: Iterable[Mapping[str, object]],
    signals: Iterable[TrendSignal],
    *,
    limit: int,
    min_images: int = 5,
) -> tuple[SelectedCatalogProduct, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if min_images <= 0:
        raise ValueError("min_images must be positive")
    ranked = []
    for record in records:
        source_key = str(record.get("source_key") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        image_count = len(record.get("image_urls") or ())
        if not source_key or not source_id or image_count < min_images:
            continue
        searchable = (
            f"{record.get('title', '')} {record.get('description', '')}".lower()
        )
        matches = [
            signal
            for signal in signals
            if any(alias.lower() in searchable for alias in signal.aliases)
        ]
        if not matches:
            continue
        signal = max(matches, key=lambda item: item.weight)
        ranked.append((signal.weight, source_key, source_id, image_count, signal))
    selected = []
    seen_families = set()
    for score, source_key, source_id, image_count, signal in sorted(
        ranked, key=lambda item: (-item[0], item[1])
    ):
        if signal.model_family in seen_families:
            continue
        seen_families.add(signal.model_family)
        selected.append(
            SelectedCatalogProduct(
                source_key=source_key,
                source_id=source_id,
                model_family=signal.model_family,
                score=score,
                image_count=image_count,
                source_urls=signal.source_urls,
            )
        )
        if len(selected) == limit:
            break
    if len(selected) != limit:
        raise ValueError(
            f"only {len(selected)} distinct eligible model families found; "
            f"requested {limit}"
        )
    return tuple(selected)


def write_selected_manifest(
    path: Path,
    records: Iterable[Mapping[str, object]],
    selected: Iterable[SelectedCatalogProduct],
) -> None:
    by_key = {str(record.get("source_key") or ""): dict(record) for record in records}
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for item in selected:
            record = by_key.get(item.source_key)
            if record is None:
                raise ValueError(f"selected source is missing: {item.source_key}")
            record["selection"] = {
                "model_family": item.model_family,
                "score": item.score,
                "source_urls": list(item.source_urls),
            }
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(output)
