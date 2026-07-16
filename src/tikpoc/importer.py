import csv
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from .models import ProfileMetrics


_REQUIRED_COLUMNS = {
    "commenter_user_id",
    "commenter_handle",
    "commenter_profile_url",
    "video_id",
}

_REQUIRED_WORKBOOK_COLUMNS = {
    "账号",
    "secUid",
    "用户ID",
    "私密账号",
    "该粉丝粉丝数",
    "该粉丝关注数",
    "视频数",
    "主页链接",
}


@dataclass(frozen=True)
class Target:
    target_id: str
    username: str
    profile_url: str
    source_video_id: str
    sec_uid: str
    profile_metrics: ProfileMetrics | None = None
    private_account: bool | None = None


@dataclass(frozen=True)
class ImportResult:
    targets: tuple[Target, ...]
    skipped_duplicates: int
    skipped_invalid: int = 0


def read_targets(path: Path) -> ImportResult:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return _read_follower_workbook(path)
    return _read_comment_export(path)


def _read_comment_export(path: Path) -> ImportResult:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or ())
        missing = sorted(_REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"missing required CSV columns: {', '.join(missing)}")

        targets: list[Target] = []
        seen_ids: set[str] = set()
        skipped_duplicates = 0
        for line_number, row in enumerate(reader, start=2):
            target_id = (row["commenter_user_id"] or "").strip()
            username = (row["commenter_handle"] or "").strip().removeprefix("@").lower()
            if not target_id or not username:
                raise ValueError(f"missing target identity on CSV line {line_number}")
            if target_id in seen_ids:
                skipped_duplicates += 1
                continue
            seen_ids.add(target_id)
            targets.append(
                Target(
                    target_id=target_id,
                    username=username,
                    profile_url=(row["commenter_profile_url"] or "").strip(),
                    source_video_id=(row["video_id"] or "").strip(),
                    sec_uid=(row.get("commenter_sec_uid") or "").strip(),
                )
            )

    return ImportResult(targets=tuple(targets), skipped_duplicates=skipped_duplicates)


def _read_follower_workbook(path: Path) -> ImportResult:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = tuple(str(value or "").strip() for value in next(rows, ()))
        columns = {name: index for index, name in enumerate(headers) if name}
        missing = sorted(_REQUIRED_WORKBOOK_COLUMNS - set(columns))
        if missing:
            raise ValueError(f"missing required workbook columns: {', '.join(missing)}")

        targets: list[Target] = []
        seen_ids: set[str] = set()
        skipped_duplicates = 0
        skipped_invalid = 0
        for row in rows:
            target_id = _cell_text(row, columns["用户ID"])
            username = _cell_text(row, columns["账号"]).removeprefix("@").lower()
            if not target_id or not username:
                skipped_invalid += 1
                continue
            if target_id in seen_ids:
                skipped_duplicates += 1
                continue
            seen_ids.add(target_id)
            targets.append(
                Target(
                    target_id=target_id,
                    username=username,
                    profile_url=_cell_text(row, columns["主页链接"]),
                    source_video_id="",
                    sec_uid=_cell_text(row, columns["secUid"]),
                    profile_metrics=ProfileMetrics(
                        following=_cell_int(row, columns["该粉丝关注数"]),
                        followers=_cell_int(row, columns["该粉丝粉丝数"]),
                        posts=_cell_int(row, columns["视频数"]),
                    ),
                    private_account=_cell_bool(row, columns["私密账号"]),
                )
            )
        return ImportResult(
            targets=tuple(targets),
            skipped_duplicates=skipped_duplicates,
            skipped_invalid=skipped_invalid,
        )
    finally:
        workbook.close()


def _cell_text(row: tuple[object, ...], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _cell_int(row: tuple[object, ...], index: int) -> int:
    raw = _cell_text(row, index).replace(",", "")
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return 0


def _cell_bool(row: tuple[object, ...], index: int) -> bool:
    return _cell_text(row, index).lower() in {"1", "true", "yes", "是"}
