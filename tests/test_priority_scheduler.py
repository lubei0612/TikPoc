import threading
import time
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import AssignmentPhase
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def _target(name: str) -> Target:
    return Target(
        target_id=f"uid-{name}",
        username=name,
        profile_url=f"https://www.tiktok.com/@{name}",
        source_video_id="",
        sec_uid=f"sec-{name}",
        identity_key=f"sec:sec-{name}",
        source_line_numbers=(1,),
    )


def _import_pool(
    repository: AcquisitionRepository, name: str, checksum_char: str, count: int = 1
) -> str:
    targets = tuple(_target(f"{name}-{index}") for index in range(count))
    return repository.import_pool(f"{name}.jsonl", checksum_char * 64, targets).pool_id


def _seeded_priority_queue(
    tmp_path: Path,
) -> tuple[AcquisitionRepository, str, str, str]:
    repository = AcquisitionRepository(tmp_path / "scheduler.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = _import_pool(repository, "ordinary", "a", count=3)
    ordinary_round = create_exposure_round(
        repository,
        pool_id=ordinary_pool,
        device_seeds={"d1": "ordinary-d1", "d2": "ordinary-d2"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    first_pool = _import_pool(repository, "priority-one", "b")
    first = repository.create_priority_batch(
        batch_id="priority-1",
        parent_round_id=ordinary_round,
        pool_id=first_pool,
        source_live_id="live-1",
        source_checksum="b" * 64,
        device_seeds={"d1": "priority-1-d1", "d2": "priority-1-d2"},
        batch_class="background",
    )
    second_pool = _import_pool(repository, "priority-two", "c")
    second = repository.create_priority_batch(
        batch_id="priority-2",
        parent_round_id=ordinary_round,
        pool_id=second_pool,
        source_live_id="live-2",
        source_checksum="c" * 64,
        device_seeds={"d1": "priority-2-d1", "d2": "priority-2-d2"},
        batch_class="background",
    )
    return repository, ordinary_round, first.priority_round_id, second.priority_round_id


def _create_live_interrupt(
    repository: AcquisitionRepository,
    ordinary_round: str,
    *,
    name: str,
    checksum_char: str,
    devices: tuple[str, ...] = ("d1", "d2"),
) -> str:
    pool = _import_pool(repository, name, checksum_char)
    batch = repository.create_priority_batch(
        batch_id=f"priority-{name}",
        parent_round_id=ordinary_round,
        pool_id=pool,
        source_live_id=name,
        source_checksum=checksum_char * 64,
        device_seeds={device: f"{name}-{device}" for device in devices},
        batch_class="live_interrupt",
    )
    return batch.priority_round_id


def _mark_assignment_terminal(
    repository: AcquisitionRepository, assignment_id: int, *, now_ms: int
) -> None:
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE round_assignments
            SET phase='completed', completed_at_ms=?,
                lease_owner=NULL, lease_expires_at_ms=0
            WHERE assignment_id=?
            """,
            (now_ms, assignment_id),
        )


def _mark_round_terminal(
    repository: AcquisitionRepository, round_id: str, *, now_ms: int
) -> None:
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE round_assignments
            SET phase='completed', completed_at_ms=?,
                lease_owner=NULL, lease_expires_at_ms=0
            WHERE round_id=?
            """,
            (now_ms, round_id),
        )


def test_scheduler_finishes_current_lease_then_prefers_oldest_priority_batch(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "current.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = _import_pool(repository, "ordinary", "a", count=2)
    ordinary_round = create_exposure_round(
        repository,
        pool_id=ordinary_pool,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    current = repository.claim_next_assignment(
        ordinary_round, "d1", "ordinary-worker", now_ms=600
    )
    assert current is not None
    priority_pool = _import_pool(repository, "priority", "b")
    priority = repository.create_priority_batch(
        batch_id="priority-1",
        parent_round_id=ordinary_round,
        pool_id=priority_pool,
        source_live_id="live-1",
        source_checksum="b" * 64,
        device_seeds={"d1": "priority-d1"},
    )

    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT lease_owner, phase, lease_expires_at_ms
            FROM round_assignments WHERE assignment_id=?
            """,
            (current.assignment_id,),
        ).fetchone()
    assert tuple(row) == (
        "ordinary-worker",
        AssignmentPhase.PROFILE_OPENING.value,
        120_600,
    )

    _mark_assignment_terminal(repository, current.assignment_id, now_ms=700)
    claimed = repository.claim_scheduled_assignment(
        ordinary_round, "d1", "priority-worker", now_ms=800
    )

    assert claimed is not None
    assert claimed.round_id == priority.priority_round_id


def test_priority_submit_and_ordinary_claim_have_atomic_order(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "atomic.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = _import_pool(repository, "ordinary", "a", count=2)
    ordinary_round = create_exposure_round(
        repository,
        pool_id=ordinary_pool,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    priority_pool = _import_pool(repository, "priority", "b")
    claim_selected = threading.Event()
    collector_started = threading.Event()
    collector_finished = threading.Event()
    original_claim = repository._claim_next_assignment_in_connection

    def paused_claim(*args, **kwargs):
        claim_selected.set()
        assert collector_started.wait(1)
        time.sleep(0.05)
        assert not collector_finished.is_set()
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(
        repository, "_claim_next_assignment_in_connection", paused_claim
    )

    def submit_priority() -> None:
        assert claim_selected.wait(1)
        collector_started.set()
        repository.create_priority_batch(
            batch_id="priority-atomic",
            parent_round_id=ordinary_round,
            pool_id=priority_pool,
            source_live_id="live-atomic",
            source_checksum="b" * 64,
            device_seeds={"d1": "priority-d1"},
        )
        collector_finished.set()

    collector = threading.Thread(target=submit_priority)
    collector.start()
    ordinary = repository.claim_scheduled_assignment(
        ordinary_round, "d1", "worker-1", now_ms=1_000
    )
    collector.join(2)

    assert ordinary is not None
    assert ordinary.round_id == ordinary_round
    assert collector_finished.is_set()
    monkeypatch.setattr(
        repository, "_claim_next_assignment_in_connection", original_claim
    )
    _mark_assignment_terminal(repository, ordinary.assignment_id, now_ms=1_100)
    priority = repository.claim_scheduled_assignment(
        ordinary_round, "d1", "worker-1", now_ms=1_200
    )
    assert priority is not None
    assert (
        priority.round_id
        == repository.priority_batch("priority-atomic").priority_round_id
    )


def test_scheduler_never_claims_second_batch_before_first_barrier(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority_one, priority_two = _seeded_priority_queue(tmp_path)

    first = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert first is not None
    assert first.round_id == priority_one

    _mark_assignment_terminal(repository, first.assignment_id, now_ms=1_100)
    waiting = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )
    assert waiting is None
    assert repository.priority_batch("priority-2").state.value == "queued"

    _mark_round_terminal(repository, priority_one, now_ms=1_300)
    second = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_400
    )
    assert second is not None
    assert second.round_id == priority_two


def test_fast_device_waits_while_another_device_has_priority_work(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority_one, _priority_two = _seeded_priority_queue(tmp_path)
    first = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert first is not None
    _mark_assignment_terminal(repository, first.assignment_id, now_ms=1_100)

    waiting = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )
    slow_device = repository.claim_scheduled_assignment(
        ordinary, "d2", "worker-2", now_ms=1_200
    )

    assert waiting is None
    assert repository.priority_batch("priority-1").state.value == "barrier"
    assert slow_device is not None
    assert slow_device.round_id == priority_one


def test_live_interrupt_preempts_unfinished_background_after_current_lease(
    tmp_path: Path,
) -> None:
    repository, ordinary, background_one, _background_two = _seeded_priority_queue(
        tmp_path
    )
    current = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert current is not None
    assert current.round_id == background_one
    live = _create_live_interrupt(
        repository,
        ordinary,
        name="live-now",
        checksum_char="d",
    )

    _mark_assignment_terminal(repository, current.assignment_id, now_ms=1_100)
    claimed = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )

    assert claimed is not None
    assert claimed.round_id == live


def test_live_interrupts_remain_fifo_before_background_resume(tmp_path: Path) -> None:
    repository, ordinary, background_one, _background_two = _seeded_priority_queue(
        tmp_path
    )
    live_one = _create_live_interrupt(
        repository,
        ordinary,
        name="live-one",
        checksum_char="d",
    )
    live_two = _create_live_interrupt(
        repository,
        ordinary,
        name="live-two",
        checksum_char="e",
    )

    first = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert first is not None and first.round_id == live_one
    _mark_round_terminal(repository, live_one, now_ms=1_100)
    second = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )
    assert second is not None and second.round_id == live_two
    _mark_round_terminal(repository, live_two, now_ms=1_300)

    resumed = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_400
    )
    assert resumed is not None
    assert resumed.round_id == background_one


def test_nonparticipant_waits_while_live_interrupt_is_active(tmp_path: Path) -> None:
    repository, ordinary, _background_one, _background_two = _seeded_priority_queue(
        tmp_path
    )
    live = _create_live_interrupt(
        repository,
        ordinary,
        name="live-d1-only",
        checksum_char="d",
        devices=("d1",),
    )

    assert (
        repository.claim_scheduled_assignment(ordinary, "d2", "worker-2", now_ms=1_000)
        is None
    )
    participant = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert participant is not None
    assert participant.round_id == live


def test_scheduler_resumes_ordinary_round_after_all_priority_batches_terminal(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority_one, priority_two = _seeded_priority_queue(tmp_path)
    _mark_round_terminal(repository, priority_one, now_ms=1_000)
    _mark_round_terminal(repository, priority_two, now_ms=1_100)

    resumed = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )

    assert resumed is not None
    assert resumed.round_id == ordinary
    assert [batch.state.value for batch in repository.priority_queue(ordinary)] == [
        "completed",
        "completed",
    ]


@pytest.mark.parametrize("state", ["paused", "completed"])
def test_scheduler_does_not_claim_when_parent_is_not_running(
    tmp_path: Path, state: str
) -> None:
    repository, ordinary, _priority_one, _priority_two = _seeded_priority_queue(
        tmp_path
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE exposure_rounds SET state=? WHERE round_id=?", (state, ordinary)
        )

    assert (
        repository.claim_scheduled_assignment(ordinary, "d1", "worker-1", now_ms=1_000)
        is None
    )


def test_scheduler_rejects_device_outside_parent_round(tmp_path: Path) -> None:
    repository, ordinary, _priority_one, _priority_two = _seeded_priority_queue(
        tmp_path
    )

    with pytest.raises(ValueError, match="device is not assigned to parent round"):
        repository.claim_scheduled_assignment(
            ordinary, "unknown-device", "worker-1", now_ms=1_000
        )


def test_scheduler_rejects_priority_round_as_parent(tmp_path: Path) -> None:
    repository, _ordinary, priority_one, _priority_two = _seeded_priority_queue(
        tmp_path
    )

    with pytest.raises(ValueError, match="scheduled parent must be an ordinary round"):
        repository.claim_scheduled_assignment(
            priority_one, "d1", "worker-1", now_ms=1_000
        )
