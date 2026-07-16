from pathlib import Path

import pytest
from openpyxl import Workbook

from tikpoc.importer import read_targets
from tikpoc.models import ProfileMetrics


HEADER = (
    "序号,source_type,video_id,video_url,commenter_user_id,commenter_sec_uid,"
    "commenter_handle,commenter_nickname,commenter_profile_url,comment_cid,"
    "comment_text,comment_like_count,reply_depth,is_second_level_reply,collected_at\n"
)


def test_read_comment_export_maps_target_fields(tmp_path: Path) -> None:
    source = tmp_path / "comments.csv"
    source.write_text(
        HEADER + "1,comment_panel,744,https://video,707,sec,@Sample,Name,"
        "https://www.tiktok.com/@Sample,,Price?,0,0,false,2026-04-15T00:00:00Z\n",
        encoding="utf-8",
    )

    result = read_targets(source)

    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.target_id == "707"
    assert target.username == "sample"
    assert target.profile_url == "https://www.tiktok.com/@Sample"
    assert target.source_video_id == "744"


def test_read_comment_export_deduplicates_user_id(tmp_path: Path) -> None:
    source = tmp_path / "comments.csv"
    source.write_text(
        HEADER
        + "1,comment_panel,744,https://video,707,sec,Sample,Name,https://profile,,a,0,0,false,now\n"
        + "2,comment_panel,744,https://video,707,sec,Changed,Name,https://profile,,b,0,1,true,now\n",
        encoding="utf-8",
    )

    result = read_targets(source)

    assert [target.username for target in result.targets] == ["sample"]
    assert result.skipped_duplicates == 1


def test_read_comment_export_requires_identity_columns(tmp_path: Path) -> None:
    source = tmp_path / "comments.csv"
    source.write_text("video_id,commenter_handle\n744,sample\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required CSV columns"):
        read_targets(source)


def test_read_follower_workbook_maps_prescreened_profile_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "followers.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "账号",
            "secUid",
            "用户ID",
            "私密账号",
            "该粉丝粉丝数",
            "该粉丝关注数",
            "视频数",
            "主页链接",
        ]
    )
    sheet.append(
        [
            "@Sample",
            "sec-1",
            "707",
            0,
            10,
            20,
            5,
            "https://www.tiktok.com/@Sample",
        ]
    )
    workbook.save(source)

    result = read_targets(source)

    assert result.skipped_duplicates == 0
    assert result.skipped_invalid == 0
    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.target_id == "707"
    assert target.username == "sample"
    assert target.sec_uid == "sec-1"
    assert target.profile_metrics == ProfileMetrics(20, 10, 5)
    assert target.private_account is False
