from dataclasses import replace
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.importer import Target


def _target(identity: str, *, lines: tuple[int, ...] = (2,)) -> Target:
    suffix = identity.removeprefix("sec:")
    return Target(
        target_id=f"user-{suffix}",
        username=f"buyer_{suffix}",
        profile_url=f"https://www.tiktok.com/@buyer_{suffix}",
        source_video_id="video-1",
        sec_uid=suffix,
        identity_key=identity,
        source_line_numbers=lines,
    )


def test_import_pool_is_idempotent_by_source_checksum(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1000)
    repository.migrate()
    checksum = "a" * 64
    targets = (_target("sec:s1", lines=(2, 4)),)

    first = repository.import_pool("comments.csv", checksum, targets)
    second = repository.import_pool("comments.csv", checksum, targets)

    assert second == first
    assert first.pool_id == "pool-aaaaaaaaaaaaaaaaaaaa"
    assert first.unique_targets == 1
    assert first.source_rows == 2
    stored = repository.pool_targets(first.pool_id)
    assert stored[0].identity_key == "sec:s1"
    assert stored[0].source_line_numbers == (2, 4)


def test_existing_pool_is_immutable(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    checksum = "a" * 64
    repository.import_pool("comments.csv", checksum, (_target("sec:s1"),))

    with pytest.raises(ValueError, match="checksum already has different content"):
        repository.import_pool("other.csv", checksum, (_target("sec:s2"),))


def test_repository_recomputes_identity_precedence(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    claimed = replace(_target("sec:s1"), identity_key="uid:claimed")

    imported = repository.import_pool("comments.csv", "b" * 64, (claimed,))

    assert repository.pool_targets(imported.pool_id)[0].identity_key == "sec:s1"
