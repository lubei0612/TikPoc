import csv
from dataclasses import dataclass
from pathlib import Path


_REQUIRED_COLUMNS = {
    "commenter_user_id",
    "commenter_handle",
    "commenter_profile_url",
    "video_id",
}


@dataclass(frozen=True)
class Target:
    target_id: str
    username: str
    profile_url: str
    source_video_id: str
    sec_uid: str


@dataclass(frozen=True)
class ImportResult:
    targets: tuple[Target, ...]
    skipped_duplicates: int


def read_targets(path: Path) -> ImportResult:
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
