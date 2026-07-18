import sqlite3
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


def test_round_coverage_and_mobile_trace_use_confirmed_assignment_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    repository = AcquisitionRepository(path, clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool(
        "comments.csv", "c" * 64, (_target("sec:s1"), _target("sec:s2"))
    )
    devices = {"phone-01": "seed-01", "phone-02": "seed-02"}
    order_keys = {
        (target.identity_key, device_id): f"{target.ordinal}-{device_id}"
        for target in repository.pool_targets(imported.pool_id)
        for device_id in devices
    }
    repository.create_round(
        round_id="round-01",
        pool_id=imported.pool_id,
        device_seeds=devices,
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
        order_keys=order_keys,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE round_assignments SET visit_confirmed_at_ms=2_000 WHERE identity_key='sec:s1'"
        )
        connection.execute(
            "UPDATE round_assignments SET visit_confirmed_at_ms=3_000 WHERE identity_key='sec:s2' AND device_id='phone-01'"
        )

    assert repository.round_coverage("round-01") == {
        "round_id": "round-01",
        "targets": 2,
        "required_devices": 2,
        "confirmed_visits": 3,
        "fully_covered": 1,
        "coverage_rate": 0.5,
    }
    traces = repository.recent_mobile_traces("round-01", limit=10)
    assert traces == [
        {
            "identity_key": "sec:s2",
            "username": "buyer_s2",
            "confirmed_devices": 1,
            "required_devices": 2,
            "fully_covered": False,
            "last_visit_confirmed_at_ms": 3_000,
        },
        {
            "identity_key": "sec:s1",
            "username": "buyer_s1",
            "confirmed_devices": 2,
            "required_devices": 2,
            "fully_covered": True,
            "last_visit_confirmed_at_ms": 2_000,
        },
    ]
