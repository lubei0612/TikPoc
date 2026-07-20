import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import AssignmentPhase, DeviceDiagnostics
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


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


def test_claim_honors_durable_device_and_assignment_control_states(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "d" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    with sqlite3.connect(repository.path) as connection:
        assignment_id = int(
            connection.execute(
                "SELECT assignment_id FROM round_assignments WHERE round_id=?",
                (round_id,),
            ).fetchone()[0]
        )

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            INSERT INTO operator_control_states(
                scope, scope_id, state, updated_at_ms, command_id
            ) VALUES ('device', 'phone-01', 'paused', 1000, 'pause-device')
            """
        )
    assert (
        repository.claim_next_assignment(
            round_id, "phone-01", "worker-01", now_ms=1_000
        )
        is None
    )

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE operator_control_states SET state='running'
            WHERE scope='device' AND scope_id='phone-01'
            """
        )
        connection.execute(
            """
            INSERT INTO operator_control_states(
                scope, scope_id, state, updated_at_ms, command_id
            ) VALUES ('assignment', ?, 'paused', 1001, 'pause-assignment')
            """,
            (str(assignment_id),),
        )
    assert (
        repository.claim_next_assignment(
            round_id, "phone-01", "worker-01", now_ms=1_001
        )
        is None
    )

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE operator_control_states SET state='running'
            WHERE scope='assignment' AND scope_id=?
            """,
            (str(assignment_id),),
        )
    claimed = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_002
    )
    assert claimed is not None
    assert claimed.assignment_id == assignment_id


def test_unreachable_assignment_is_terminal_without_confirmed_coverage(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "e" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    claimed = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert claimed is not None
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE round_assignments SET attempt_count = 3 WHERE assignment_id = ?",
            (claimed.assignment_id,),
        )

    skipped = repository.skip_unreachable_assignment(
        claimed.assignment_id,
        "worker-01",
        now_ms=1_100,
        error_code="profile_unreachable",
        original_error_code="ValueError",
        failure_stage="route",
        diagnostics=DeviceDiagnostics(
            screenshot_path="screenshots/unreachable.png",
            ui_summary="profile route remained blank",
        ),
    )

    assert skipped.phase is AssignmentPhase.SKIPPED
    assert skipped.visit_confirmed_at_ms is None
    assert skipped.completed_at_ms == 1_100
    assert skipped.last_error_code == "profile_unreachable"
    assert skipped.lease_owner is None
    assert (
        repository.claim_next_assignment(
            round_id, "phone-01", "worker-02", now_ms=1_101
        )
        is None
    )
    completion = repository.round_completion(round_id)
    assert completion.completed == 0
    assert completion.skipped == 1
    assert repository.round_coverage(round_id)["confirmed_visits"] == 0
    transition = repository.assignment_phase_history(claimed.assignment_id)[-1]
    assert transition.to_phase is AssignmentPhase.SKIPPED
    assert transition.details == {
        "attempt_count": 3,
        "error_code": "profile_unreachable",
        "failure_stage": "route",
        "original_error_code": "ValueError",
        "screenshot_path": "screenshots/unreachable.png",
        "ui_summary": "profile route remained blank",
    }
    with sqlite3.connect(repository.path) as connection:
        state = connection.execute(
            "SELECT state FROM exposure_rounds WHERE round_id = ?", (round_id,)
        ).fetchone()[0]
    assert state == "completed"


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
