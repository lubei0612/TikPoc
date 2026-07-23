import sqlite3
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import AssignmentPhase
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def _repository_with_targets(
    tmp_path: Path, count: int
) -> tuple[AcquisitionRepository, str]:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 500)
    repository.migrate()
    targets = tuple(
        Target(
            target_id=f"user-{index}",
            username=f"buyer_{index}",
            profile_url=f"https://www.tiktok.com/@buyer_{index}",
            source_video_id="video-1",
            sec_uid=f"s{index}",
            identity_key=f"sec:s{index}",
            source_line_numbers=(index + 2,),
        )
        for index in range(count)
    )
    imported = repository.import_pool("comments.csv", f"{count:064x}", targets)
    return repository, imported.pool_id


def test_round_materializes_every_target_for_every_device(tmp_path: Path) -> None:
    repository, pool_id = _repository_with_targets(tmp_path, count=3)
    round_id = create_exposure_round(
        repository,
        pool_id=pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
    )

    assert repository.assignment_count(round_id) == 6
    first_order = repository.device_target_order(round_id, "phone-01")
    second_order = repository.device_target_order(round_id, "phone-02")
    assert set(first_order) == set(second_order)
    assert first_order != second_order


def test_assignment_schema_indexes_global_target_activity(tmp_path: Path) -> None:
    repository, _ = _repository_with_targets(tmp_path, count=1)
    with sqlite3.connect(repository.path) as connection:
        index_names = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(round_assignments)")
        }
    assert "round_assignment_target_activity_idx" in index_names


def test_order_is_stable_across_repository_restart(tmp_path: Path) -> None:
    repository, pool_id = _repository_with_targets(tmp_path, count=20)
    round_id = create_exposure_round(
        repository,
        pool_id=pool_id,
        device_seeds={"phone-01": "seed-a"},
        starts_at_ms=1_000,
    )
    before = repository.device_target_order(round_id, "phone-01")

    reopened = AcquisitionRepository(repository.path)
    reopened.migrate()

    assert reopened.device_target_order(round_id, "phone-01") == before


def test_round_rejects_duplicate_device_seeds(tmp_path: Path) -> None:
    repository, pool_id = _repository_with_targets(tmp_path, count=1)
    with pytest.raises(ValueError, match="device order seeds must be unique"):
        create_exposure_round(
            repository,
            pool_id=pool_id,
            device_seeds={"phone-01": "same", "phone-02": "same"},
            starts_at_ms=1_000,
        )


def test_round_rejects_device_ids_that_collide_after_normalization(
    tmp_path: Path,
) -> None:
    repository, pool_id = _repository_with_targets(tmp_path, count=1)
    with pytest.raises(ValueError, match="device ids must be unique"):
        create_exposure_round(
            repository,
            pool_id=pool_id,
            device_seeds={" phone-01": "seed-a", "phone-01": "seed-b"},
            starts_at_ms=1_000,
        )


def test_claims_prevent_simultaneous_and_recent_target_visits(tmp_path: Path) -> None:
    repository, pool_id = _repository_with_targets(tmp_path, count=1)
    round_id = create_exposure_round(
        repository,
        pool_id=pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=100,
    )

    first = repository.claim_next_assignment(
        round_id, "phone-01", "worker-1", now_ms=1_000, lease_ttl_ms=50
    )
    assert first is not None
    assert (
        repository.claim_next_assignment(
            round_id, "phone-02", "worker-2", now_ms=1_001, lease_ttl_ms=50
        )
        is None
    )

    repository.record_visit_confirmed(first.assignment_id, "worker-1", now_ms=1_010)
    repository.release_assignment_lease(first.assignment_id, "worker-1")
    assert (
        repository.claim_next_assignment(
            round_id, "phone-02", "worker-2", now_ms=1_109, lease_ttl_ms=50
        )
        is None
    )
    second = repository.claim_next_assignment(
        round_id, "phone-02", "worker-2", now_ms=1_110, lease_ttl_ms=50
    )
    assert second is not None
    assert second.identity_key == first.identity_key


def test_active_target_lock_applies_across_different_pools(tmp_path: Path) -> None:
    repository, first_pool_id = _repository_with_targets(tmp_path, count=1)
    target = repository.pool_targets(first_pool_id)[0]
    second = Target(
        target_id=target.target_id,
        username=target.username,
        profile_url=target.profile_url,
        source_video_id="video-2",
        sec_uid=target.sec_uid,
        identity_key=target.identity_key,
        source_line_numbers=(20,),
    )
    second_pool_id = repository.import_pool(
        "other-comments.csv", "f" * 64, (second,)
    ).pool_id
    first_round = create_exposure_round(
        repository,
        pool_id=first_pool_id,
        device_seeds={"phone-01": "seed-a"},
        starts_at_ms=1_000,
    )
    second_round = create_exposure_round(
        repository,
        pool_id=second_pool_id,
        device_seeds={"phone-02": "seed-b"},
        starts_at_ms=1_000,
    )

    assert (
        repository.claim_next_assignment(
            first_round, "phone-01", "worker-1", now_ms=1_000
        )
        is not None
    )
    assert (
        repository.claim_next_assignment(
            second_round, "phone-02", "worker-2", now_ms=1_001
        )
        is None
    )


def test_expired_assignment_lease_returns_to_pending(tmp_path: Path) -> None:
    repository, pool_id = _repository_with_targets(tmp_path, count=1)
    round_id = create_exposure_round(
        repository,
        pool_id=pool_id,
        device_seeds={"phone-01": "seed-a"},
        starts_at_ms=1_000,
    )
    claimed = repository.claim_next_assignment(
        round_id, "phone-01", "worker-1", now_ms=1_000, lease_ttl_ms=50
    )
    assert claimed is not None

    assert repository.recover_expired_assignment_leases(now_ms=1_049) == 0
    assert repository.recover_expired_assignment_leases(now_ms=1_050) == 1
    assert repository.assignment(claimed.assignment_id).phase is AssignmentPhase.PENDING
    transition = repository.assignment_phase_history(claimed.assignment_id)[-1]
    assert transition.from_phase is AssignmentPhase.PROFILE_OPENING
    assert transition.to_phase is AssignmentPhase.PENDING
    assert transition.details["reason"] == "lease_expired"
    assert (
        repository.claim_next_assignment(
            round_id, "phone-01", "worker-2", now_ms=1_051, lease_ttl_ms=50
        )
        is not None
    )
