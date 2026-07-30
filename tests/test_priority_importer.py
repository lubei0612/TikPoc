# ruff: noqa: FLY002
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from tikpoc.priority_importer import read_priority_targets


def test_priority_jsonl_merges_username_and_upgrades_identity(tmp_path: Path) -> None:
    source = tmp_path / "live.jsonl"
    rows = (
        {
            "username": "@Buyer.One",
            "user_id": "dom-0-buyer.one",
            "source_live_id": "live-1",
        },
        {
            "username": "buyer.one",
            "user_id": "real-1",
            "source_live_id": "live-1",
        },
        {
            "username": "BUYER.ONE",
            "user_id": "real-1",
            "sec_uid": "sec-1",
            "source_live_id": "live-1",
        },
    )
    source.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-1")

    assert result.source_rows == 3
    assert result.skipped_duplicates == 2
    assert result.skipped_invalid == 0
    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.username == "buyer.one"
    assert target.target_id == "real-1"
    assert target.sec_uid == "sec-1"
    assert target.identity_key == "sec:sec-1"
    assert target.profile_url == "https://www.tiktok.com/@buyer.one"
    assert target.source_line_numbers == (1, 2, 3)


def test_priority_jsonl_deduplicates_renamed_handle_by_sec_uid(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text(
        "\n".join(
            (
                '{"username":"old.name","sec_uid":"sec-shared"}',
                '{"username":"new.name","sec_uid":"sec-shared"}',
            )
        ),
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-2")

    assert len(result.targets) == 1
    assert result.targets[0].identity_key == "sec:sec-shared"
    assert result.targets[0].source_line_numbers == (1, 2)
    assert result.skipped_duplicates == 1


@pytest.mark.parametrize(
    ("first", "second", "field"),
    [
        (
            {"username": "buyer", "sec_uid": "sec-1"},
            {"username": "buyer", "sec_uid": "sec-2"},
            "sec_uid",
        ),
        (
            {"username": "buyer", "user_id": "uid-1"},
            {"username": "buyer", "user_id": "uid-2"},
            "user_id",
        ),
    ],
)
def test_priority_jsonl_rejects_conflicting_stable_identity(
    tmp_path: Path, first: dict[str, str], second: dict[str, str], field: str
) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text(
        "\n".join((json.dumps(first), json.dumps(second))),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"source line 2.*conflicting {field}"):
        read_priority_targets(source, source_live_id="live-conflict")


def test_priority_jsonl_counts_alias_bridge_collapses_as_duplicates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text(
        "\n".join(
            (
                '{"username":"alice","user_id":"uid-1"}',
                '{"username":"bob","sec_uid":"sec-1"}',
                '{"username":"alice","sec_uid":"sec-1"}',
            )
        ),
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-bridge")

    assert result.source_rows == 3
    assert len(result.targets) == 1
    assert result.skipped_duplicates == 2
    assert result.source_rows == (
        len(result.targets) + result.skipped_duplicates + result.skipped_invalid
    )


def test_priority_jsonl_counts_missing_username_as_invalid(tmp_path: Path) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text(
        '{"user_id":"real-1"}\n{"username":"valid"}\n',
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-3")

    assert [target.username for target in result.targets] == ["valid"]
    assert result.source_rows == 2
    assert result.skipped_invalid == 1


def test_priority_jsonl_rejects_malformed_row_with_line_number(tmp_path: Path) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text('{"username":"valid"}\n{bad json}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="JSONL line 2"):
        read_priority_targets(source, source_live_id="live-4")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("username", {"unexpected": "object"}),
        ("user_id", ["unexpected", "array"]),
        ("profile_url", {"unexpected": "object"}),
    ],
)
def test_priority_jsonl_rejects_non_string_machine_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    source = tmp_path / "live.jsonl"
    row = {"username": "buyer", field: value}
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=rf"JSONL line 1.*{field}.*string or null"):
        read_priority_targets(source, source_live_id="live-schema")


def test_priority_jsonl_counts_invalid_tiktok_handle(tmp_path: Path) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text(
        "\n".join(("{}", '{"username":"bad/name"}', '{"username":"valid.name"}')),
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-handle")

    assert [target.username for target in result.targets] == ["valid.name"]
    assert result.skipped_invalid == 2


def test_priority_jsonl_rejects_non_tiktok_profile_url(tmp_path: Path) -> None:
    source = tmp_path / "live.jsonl"
    source.write_text(
        '{"username":"buyer","profile_url":"https://example.com/@buyer"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source line 1.*TikTok profile URL"):
        read_priority_targets(source, source_live_id="live-url")


def test_priority_importer_accepts_current_english_follower_headers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "followers.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "page_url",
            "profile_handle",
            "follower_uid",
            "follower_sec_uid",
            "follower_handle",
            "crawl_time",
            "data_source",
        ]
    )
    sheet.append(
        [
            "https://www.tiktok.com/@source",
            "source",
            "dom-0-sample",
            None,
            "Sample",
            "2026-07-22T20:00:00Z",
            "dom",
        ]
    )
    workbook.save(source)

    result = read_priority_targets(source, source_live_id="live-5")

    assert result.source_rows == 1
    assert result.skipped_invalid == 0
    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.username == "sample"
    assert target.target_id == ""
    assert target.sec_uid == ""
    assert target.identity_key == "handle:sample"
    assert target.profile_url == "https://www.tiktok.com/@sample"
    assert target.source_line_numbers == (2,)


def test_priority_importer_requires_supported_named_headers(tmp_path: Path) -> None:
    source = tmp_path / "followers.xlsx"
    workbook = Workbook()
    workbook.active.append(["username", "id"])
    workbook.active.append(["sample", "123"])
    workbook.save(source)

    with pytest.raises(ValueError, match="missing required priority workbook columns"):
        read_priority_targets(source, source_live_id="live-6")


def test_priority_jsonl_accepts_followers_source_contract(tmp_path: Path) -> None:
    source = tmp_path / "followers.jsonl"
    source.write_text(
        json.dumps(
            {
                "username": "buyer.one",
                "user_id": "123",
                "sec_uid": "sec-1",
                "source_type": "comments",
                "source_id": "video-1",
                "collected_at": "2026-07-26T20:00:00+08:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="video-1")

    assert [target.username for target in result.targets] == ["buyer.one"]


def test_priority_jsonl_accepts_qualified_live_audience_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live-audience.jsonl"
    source.write_text(
        json.dumps(
            {
                "username": "buyer.one",
                "source_type": "live_audience",
                "source_id": "live-1",
                "lead_level": "A",
                "qualification_reasons": ["comment"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-1")

    assert [target.username for target in result.targets] == ["buyer.one"]


def test_priority_jsonl_rejects_explicit_c_level_audience(tmp_path: Path) -> None:
    source = tmp_path / "live-audience.jsonl"
    source.write_text(
        json.dumps(
            {
                "username": "passive.viewer",
                "lead_level": "C",
                "qualification_reasons": ["join_only"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="JSONL line 1.*lead_level.*A or B"):
        read_priority_targets(source, source_live_id="live-2")


@pytest.mark.parametrize(
    ("row", "exception_type", "message"),
    [
        ({"lead_level": "A"}, ValueError, "qualification_reasons.*required"),
        ({"qualification_reasons": ["comment"]}, ValueError, "lead_level.*required"),
        (
            {"lead_level": "A", "qualification_reasons": "comment"},
            TypeError,
            "qualification_reasons.*array",
        ),
        (
            {"lead_level": "A", "qualification_reasons": []},
            ValueError,
            "qualification_reasons.*nonempty",
        ),
        (
            {"lead_level": "A", "qualification_reasons": [""]},
            ValueError,
            "qualification_reasons.*nonempty strings",
        ),
        (
            {"lead_level": "A", "qualification_reasons": ["join_only"]},
            ValueError,
            "qualification_reasons.*unsupported",
        ),
    ],
)
def test_priority_jsonl_rejects_malformed_quality_metadata(
    tmp_path: Path,
    row: dict[str, object],
    exception_type: type[Exception],
    message: str,
) -> None:
    source = tmp_path / "live-audience.jsonl"
    source.write_text(
        json.dumps({"username": "buyer", **row}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(exception_type, match=rf"JSONL line 1.*{message}"):
        read_priority_targets(source, source_live_id="live-3")


def test_priority_jsonl_rejects_different_source_id(tmp_path: Path) -> None:
    source = tmp_path / "followers.jsonl"
    source.write_text(
        '{"username":"buyer","source_type":"live","source_id":"room-2"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="different source_id"):
        read_priority_targets(source, source_live_id="room-1")
