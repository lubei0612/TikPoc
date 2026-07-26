import sqlite3
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_service import AcquisitionConflict, AcquisitionNotFound
from tikpoc.importer import Target
from tikpoc.operator_control_repository import OperatorControlRepository
from tikpoc.rounds import create_exposure_round


def _seed(path: Path) -> tuple[str, int]:
    acquisition = AcquisitionRepository(path, clock_ms=lambda: 1_000)
    acquisition.migrate()
    target = Target(
        target_id="buyer-1",
        username="buyer_1",
        profile_url="https://www.tiktok.com/@buyer_1",
        source_video_id="video-1",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )
    pool = acquisition.import_pool("targets.csv", "a" * 64, (target,))
    round_id = create_exposure_round(
        acquisition,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    with sqlite3.connect(path) as connection:
        assignment_id = int(
            connection.execute(
                "SELECT assignment_id FROM round_assignments"
            ).fetchone()[0]
        )
    return round_id, assignment_id


def test_operator_repository_persists_and_replays_control_commands(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    round_id, _ = _seed(path)
    repository = OperatorControlRepository(path, clock_ms=lambda: 2_000)
    repository.migrate()

    first = repository.apply_command("pause", "command-1", "round", round_id)
    replay = repository.apply_command("pause", "command-1", "round", round_id)

    assert (
        first
        == replay
        == {
            "command_id": "command-1",
            "command": "pause",
            "scope": "round",
            "scope_id": round_id,
            "state": "paused",
        }
    )


def test_operator_repository_replays_failure_after_state_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    _, assignment_id = _seed(path)
    repository = OperatorControlRepository(path, clock_ms=lambda: 2_000)
    repository.migrate()

    with pytest.raises(AcquisitionConflict, match="assignment is not retryable"):
        repository.retry("retry-1", assignment_id)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE round_assignments SET phase='deferred' WHERE assignment_id=?",
            (assignment_id,),
        )
    with pytest.raises(AcquisitionConflict, match="assignment is not retryable"):
        repository.retry("retry-1", assignment_id)


def test_operator_repository_maps_missing_target_to_stable_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    _seed(path)
    repository = OperatorControlRepository(path, clock_ms=lambda: 2_000)
    repository.migrate()

    with pytest.raises(AcquisitionNotFound, match="command target not found"):
        repository.apply_command("start", "missing-1", "round", "missing")
