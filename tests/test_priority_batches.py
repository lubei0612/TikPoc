from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import PriorityBatchClass
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def _target(name: str, *, sec_uid: str | None = None) -> Target:
    normalized_sec_uid = sec_uid or f"sec-{name}"
    return Target(
        target_id=f"uid-{name}",
        username=name,
        profile_url=f"https://www.tiktok.com/@{name}",
        source_video_id="",
        sec_uid=normalized_sec_uid,
        identity_key=f"sec:{normalized_sec_uid}",
        source_line_numbers=(1,),
    )


def _seeded_repository(
    tmp_path: Path,
) -> tuple[AcquisitionRepository, str, str]:
    repository = AcquisitionRepository(tmp_path / "priority.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "a" * 64, (_target("ordinary"),)
    )
    ordinary_round = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-1", "d2": "ordinary-2"},
        starts_at_ms=100,
        min_inter_device_gap_ms=75,
        min_repeat_gap_ms=125,
    )
    priority_pool = repository.import_pool(
        "live.jsonl", "b" * 64, (_target("buyer-one"), _target("buyer-two"))
    )
    return repository, ordinary_round, priority_pool.pool_id


def _create_batch(
    repository: AcquisitionRepository,
    ordinary_round: str,
    pool_id: str,
    **overrides,
):
    arguments = {
        "batch_id": "priority-1",
        "parent_round_id": ordinary_round,
        "pool_id": pool_id,
        "source_live_id": "live-1",
        "source_checksum": "b" * 64,
        "device_seeds": {"d1": "priority-1-d1", "d2": "priority-1-d2"},
    }
    arguments.update(overrides)
    return repository.create_priority_batch(**arguments)


def test_create_priority_batch_snapshots_round_devices_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)

    first = _create_batch(repository, ordinary_round, priority_pool)
    second = _create_batch(repository, ordinary_round, priority_pool)

    assert first == second
    assert first.batch_id == "priority-1"
    assert first.parent_round_id == ordinary_round
    assert first.source_live_id == "live-1"
    assert first.state.value == "queued"
    assert repository.priority_batch_device_ids("priority-1") == ("d1", "d2")
    assert repository.priority_queue(ordinary_round) == (first,)
    assert repository.assignment_count(first.priority_round_id) == 4


def test_priority_batch_uses_distinct_deterministic_order_per_device(
    tmp_path: Path,
) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)

    batch = _create_batch(repository, ordinary_round, priority_pool)

    first_order = repository.device_target_order(batch.priority_round_id, "d1")
    second_order = repository.device_target_order(batch.priority_round_id, "d2")
    assert set(first_order) == set(second_order)
    assert first_order != second_order


def test_priority_batches_receive_fifo_sequence(tmp_path: Path) -> None:
    repository, ordinary_round, first_pool = _seeded_repository(tmp_path)
    first = _create_batch(repository, ordinary_round, first_pool)
    second_pool = repository.import_pool(
        "live-two.jsonl", "c" * 64, (_target("buyer-three"),)
    )

    second = _create_batch(
        repository,
        ordinary_round,
        second_pool.pool_id,
        batch_id="priority-2",
        source_live_id="live-2",
        source_checksum="c" * 64,
        device_seeds={"d1": "priority-2-d1", "d2": "priority-2-d2"},
    )

    assert first.queue_sequence < second.queue_sequence
    assert repository.priority_queue(ordinary_round) == (first, second)


def test_priority_batch_replay_rejects_different_content(tmp_path: Path) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)
    _create_batch(repository, ordinary_round, priority_pool)

    with pytest.raises(ValueError, match="batch id already has different content"):
        _create_batch(
            repository,
            ordinary_round,
            priority_pool,
            source_live_id="different-live",
        )

    assert len(repository.priority_queue(ordinary_round)) == 1


def test_priority_batch_requires_exact_parent_device_snapshot(tmp_path: Path) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)

    with pytest.raises(ValueError, match="device seeds must match parent round"):
        _create_batch(
            repository,
            ordinary_round,
            priority_pool,
            device_seeds={"d1": "priority-only-d1"},
            batch_class="background",
        )


def test_live_interrupt_accepts_nonempty_parent_device_subset(tmp_path: Path) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)

    batch = _create_batch(
        repository,
        ordinary_round,
        priority_pool,
        device_seeds={"d1": "priority-only-d1"},
        batch_class="live_interrupt",
    )

    assert batch.batch_class is PriorityBatchClass.LIVE_INTERRUPT
    assert repository.priority_batch_device_ids(batch.batch_id) == ("d1",)


def test_background_batch_records_class_and_requires_full_parent(
    tmp_path: Path,
) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)

    batch = _create_batch(
        repository,
        ordinary_round,
        priority_pool,
        batch_class="background",
    )

    assert batch.batch_class is PriorityBatchClass.BACKGROUND


def test_priority_batch_rejects_terminal_parent_round(tmp_path: Path) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE exposure_rounds SET state='completed' WHERE round_id=?",
            (ordinary_round,),
        )

    with pytest.raises(ValueError, match="parent round is terminal"):
        _create_batch(repository, ordinary_round, priority_pool)


def test_priority_batch_rejects_missing_parent_round(tmp_path: Path) -> None:
    repository, _ordinary_round, priority_pool = _seeded_repository(tmp_path)

    with pytest.raises(ValueError, match="parent round does not exist"):
        _create_batch(repository, "round-missing", priority_pool)


def test_priority_batch_rejects_parent_round_without_devices(tmp_path: Path) -> None:
    repository, _ordinary_round, priority_pool = _seeded_repository(tmp_path)
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO exposure_rounds(
                round_id, pool_id, state, starts_at_ms,
                min_inter_device_gap_ms, min_repeat_gap_ms, created_at_ms
            ) VALUES ('round-empty', ?, 'pending', 100, 0, 0, 100)
            """,
            (priority_pool,),
        )

    with pytest.raises(ValueError, match="parent round has no devices"):
        _create_batch(
            repository,
            "round-empty",
            priority_pool,
            device_seeds={"d1": "priority-empty-d1"},
        )


def test_priority_batch_rejects_priority_round_as_parent(tmp_path: Path) -> None:
    repository, ordinary_round, first_pool = _seeded_repository(tmp_path)
    first = _create_batch(repository, ordinary_round, first_pool)
    second_pool = repository.import_pool("nested.jsonl", "c" * 64, (_target("nested"),))

    with pytest.raises(ValueError, match="parent round must be an ordinary round"):
        _create_batch(
            repository,
            first.priority_round_id,
            second_pool.pool_id,
            batch_id="priority-nested",
            source_live_id="live-nested",
            source_checksum="c" * 64,
            device_seeds={"d1": "nested-d1", "d2": "nested-d2"},
        )


def test_priority_batch_persists_across_repository_restart(tmp_path: Path) -> None:
    repository, ordinary_round, priority_pool = _seeded_repository(tmp_path)
    created = _create_batch(repository, ordinary_round, priority_pool)

    reopened = AcquisitionRepository(repository.path, clock_ms=lambda: 900)
    reopened.migrate()

    assert reopened.priority_batch("priority-1") == created
    assert reopened.priority_queue(ordinary_round) == (created,)
