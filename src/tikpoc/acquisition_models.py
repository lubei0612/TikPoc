from dataclasses import dataclass


@dataclass(frozen=True)
class PoolImport:
    pool_id: str
    unique_targets: int
    source_rows: int


@dataclass(frozen=True)
class PoolTarget:
    pool_id: str
    identity_key: str
    target_id: str
    sec_uid: str
    username: str
    profile_url: str
    source_video_id: str
    source_line_numbers: tuple[int, ...]
    ordinal: int
