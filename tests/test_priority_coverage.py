from pathlib import Path

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import AssignmentPhase, DeviceDiagnostics
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def _target(
    username: str,
    *,
    target_id: str = "",
    sec_uid: str = "",
    identity_key: str | None = None,
) -> Target:
    key = identity_key or (
        f"sec:{sec_uid}"
        if sec_uid
        else f"uid:{target_id}"
        if target_id
        else f"handle:{username.lower()}"
    )
    return Target(
        target_id=target_id,
        username=username,
        profile_url=f"https://www.tiktok.com/@{username}",
        source_video_id="",
        sec_uid=sec_uid,
        identity_key=key,
        source_line_numbers=(1,),
    )


def _seeded_matching_identity_rounds(
    tmp_path: Path,
) -> tuple[AcquisitionRepository, str, str]:
    repository = AcquisitionRepository(tmp_path / "coverage.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv",
        "a" * 64,
        (_target("Buyer.One"),),
    )
    ordinary_round = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1", "d2": "ordinary-d2"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    priority_pool = repository.import_pool(
        "live.jsonl",
        "b" * 64,
        (
            _target(
                "buyer.one",
                target_id="real-123",
                sec_uid="sec-123",
                identity_key="sec:sec-123",
            ),
        ),
    )
    priority = repository.create_priority_batch(
        batch_id="priority-1",
        parent_round_id=ordinary_round,
        pool_id=priority_pool.pool_id,
        source_live_id="live-1",
        source_checksum="b" * 64,
        device_seeds={"d1": "priority-d1", "d2": "priority-d2"},
    )
    return repository, ordinary_round, priority.priority_round_id


def _assignment_for(repository: AcquisitionRepository, round_id: str, device_id: str):
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT assignment_id FROM round_assignments
            WHERE round_id = ? AND device_id = ?
            """,
            (round_id, device_id),
        ).fetchone()
    assert row is not None
    return repository.assignment(int(row["assignment_id"]))


def _complete_confirmed(
    repository: AcquisitionRepository,
    assignment_id: int,
    owner_id: str,
    *,
    now_ms: int,
) -> None:
    repository.record_visit_confirmed(assignment_id, owner_id, now_ms=now_ms)
    repository.complete_assignment(
        assignment_id,
        owner_id,
        AssignmentPhase.IDENTITY_CONFIRMED,
        now_ms=now_ms,
    )


def test_priority_completion_satisfies_only_matching_parent_device(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority = _seeded_matching_identity_rounds(tmp_path)
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == priority

    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    parent_d1 = _assignment_for(repository, ordinary, "d1")
    parent_d2 = _assignment_for(repository, ordinary, "d2")
    assert parent_d1.phase is AssignmentPhase.COMPLETED
    assert parent_d1.visit_confirmed_at_ms == 1_100
    assert parent_d2.phase is AssignmentPhase.PENDING
    assert parent_d2.visit_confirmed_at_ms is None
    history = repository.assignment_phase_history(parent_d1.assignment_id)
    assert history[-1].details == {
        "reason": "satisfied_by_priority",
        "source_assignment_id": assignment.assignment_id,
    }


def test_existing_ordinary_visit_satisfies_matching_priority_assignment(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "existing.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "c" * 64, (_target("Buyer.One"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1", "d2": "ordinary-d2"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    ordinary_assignment = repository.claim_next_assignment(
        ordinary, "d1", "ordinary-worker", now_ms=700
    )
    assert ordinary_assignment is not None
    _complete_confirmed(
        repository,
        ordinary_assignment.assignment_id,
        "ordinary-worker",
        now_ms=800,
    )
    priority_pool = repository.import_pool(
        "live.jsonl",
        "d" * 64,
        (
            _target(
                "buyer.one",
                target_id="real-123",
                sec_uid="sec-123",
                identity_key="sec:sec-123",
            ),
        ),
    )

    priority = repository.create_priority_batch(
        batch_id="priority-existing",
        parent_round_id=ordinary,
        pool_id=priority_pool.pool_id,
        source_live_id="live-existing",
        source_checksum="d" * 64,
        device_seeds={"d1": "priority-d1", "d2": "priority-d2"},
    )

    priority_d1 = _assignment_for(repository, priority.priority_round_id, "d1")
    priority_d2 = _assignment_for(repository, priority.priority_round_id, "d2")
    assert priority_d1.phase is AssignmentPhase.COMPLETED
    assert priority_d1.visit_confirmed_at_ms == 800
    assert priority_d2.phase is AssignmentPhase.PENDING
    assert priority_d2.visit_confirmed_at_ms is None
    history = repository.assignment_phase_history(priority_d1.assignment_id)
    assert history[-1].details == {
        "reason": "satisfied_by_parent",
        "source_assignment_id": ordinary_assignment.assignment_id,
    }


def test_skipped_priority_target_does_not_create_confirmed_ordinary_coverage(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority = _seeded_matching_identity_rounds(tmp_path)
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == priority

    repository.skip_unreachable_assignment(
        assignment.assignment_id,
        "worker-1",
        now_ms=1_100,
        error_code="profile_unreachable",
        original_error_code="profile_missing",
        failure_stage="route",
        diagnostics=DeviceDiagnostics(ui_summary="profile unavailable"),
    )

    skipped = repository.assignment(assignment.assignment_id)
    parent = _assignment_for(repository, ordinary, "d1")
    assert skipped.phase is AssignmentPhase.SKIPPED
    assert skipped.visit_confirmed_at_ms is None
    assert parent.phase is AssignmentPhase.PENDING
    assert parent.visit_confirmed_at_ms is None


def test_priority_completion_does_not_match_different_parent_identity(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority = _seeded_matching_identity_rounds(tmp_path)
    with repository._connect() as connection:
        ordinary_pool = connection.execute(
            "SELECT pool_id FROM exposure_rounds WHERE round_id = ?", (ordinary,)
        ).fetchone()["pool_id"]
        connection.execute(
            """
            UPDATE pool_targets SET username = 'different.user'
            WHERE pool_id = ?
            """,
            (ordinary_pool,),
        )
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == priority

    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    parent = _assignment_for(repository, ordinary, "d1")
    assert parent.phase is AssignmentPhase.PENDING
    assert parent.visit_confirmed_at_ms is None


def test_priority_completion_does_not_propagate_ambiguous_identity_aliases(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "ambiguous.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv",
        "e" * 64,
        (
            _target("buyer.one", identity_key="handle:buyer.one"),
            _target(
                "renamed.buyer",
                sec_uid="sec-123",
                identity_key="sec:sec-123",
            ),
        ),
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    priority_pool = repository.import_pool(
        "live.jsonl",
        "f" * 64,
        (
            _target(
                "buyer.one",
                sec_uid="sec-123",
                identity_key="priority:buyer-one",
            ),
        ),
    )
    priority = repository.create_priority_batch(
        batch_id="priority-ambiguous",
        parent_round_id=ordinary,
        pool_id=priority_pool.pool_id,
        source_live_id="live-ambiguous",
        source_checksum="f" * 64,
        device_seeds={"d1": "priority-d1"},
    )
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == priority.priority_round_id

    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    with repository._connect() as connection:
        parent_rows = connection.execute(
            """
            SELECT phase, visit_confirmed_at_ms FROM round_assignments
            WHERE round_id = ? ORDER BY assignment_id
            """,
            (ordinary,),
        ).fetchall()
    assert [(row["phase"], row["visit_confirmed_at_ms"]) for row in parent_rows] == [
        (AssignmentPhase.PENDING.value, None),
        (AssignmentPhase.PENDING.value, None),
    ]


def test_priority_completion_does_not_overwrite_deferred_uncertain_parent(
    tmp_path: Path,
) -> None:
    repository, ordinary, priority = _seeded_matching_identity_rounds(tmp_path)
    parent = _assignment_for(repository, ordinary, "d1")
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'deferred', visit_confirmed_at_ms = 700,
                last_error_code = 'action_uncertain'
            WHERE assignment_id = ?
            """,
            (parent.assignment_id,),
        )
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == priority

    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    unchanged = repository.assignment(parent.assignment_id)
    assert unchanged.phase is AssignmentPhase.DEFERRED
    assert unchanged.visit_confirmed_at_ms == 700
    assert unchanged.last_error_code == "action_uncertain"


def test_priority_completion_rejects_conflicting_stable_ids(tmp_path: Path) -> None:
    repository, ordinary, priority = _seeded_matching_identity_rounds(tmp_path)
    with repository._connect() as connection:
        ordinary_pool = connection.execute(
            "SELECT pool_id FROM exposure_rounds WHERE round_id = ?", (ordinary,)
        ).fetchone()["pool_id"]
        connection.execute(
            """
            UPDATE pool_targets
            SET sec_uid = 'different-sec', target_id = 'different-id'
            WHERE pool_id = ?
            """,
            (ordinary_pool,),
        )
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == priority

    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    parent = _assignment_for(repository, ordinary, "d1")
    assert parent.phase is AssignmentPhase.PENDING
    assert parent.visit_confirmed_at_ms is None


def test_parent_stays_schedulable_until_all_priority_batches_finish(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "queue.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "1" * 64, (_target("buyer.one"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    first_pool = repository.import_pool(
        "first.jsonl", "2" * 64, (_target("buyer.one"),)
    )
    first = repository.create_priority_batch(
        batch_id="priority-first",
        parent_round_id=ordinary,
        pool_id=first_pool.pool_id,
        source_live_id="live-first",
        source_checksum="2" * 64,
        device_seeds={"d1": "priority-first-d1"},
    )
    second_pool = repository.import_pool(
        "second.jsonl", "3" * 64, (_target("buyer.two"),)
    )
    second = repository.create_priority_batch(
        batch_id="priority-second",
        parent_round_id=ordinary,
        pool_id=second_pool.pool_id,
        source_live_id="live-second",
        source_checksum="3" * 64,
        device_seeds={"d1": "priority-second-d1"},
    )
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == first.priority_round_id
    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    next_assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )

    assert next_assignment is not None
    assert next_assignment.round_id == second.priority_round_id


def test_completed_source_without_visit_does_not_satisfy_priority(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "no-visit.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "4" * 64, (_target("buyer.one"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    parent = _assignment_for(repository, ordinary, "d1")
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE round_assignments SET phase = 'completed', completed_at_ms = 700
            WHERE assignment_id = ?
            """,
            (parent.assignment_id,),
        )
    priority_pool = repository.import_pool(
        "live.jsonl", "5" * 64, (_target("buyer.one"),)
    )

    priority = repository.create_priority_batch(
        batch_id="priority-no-visit",
        parent_round_id=ordinary,
        pool_id=priority_pool.pool_id,
        source_live_id="live-no-visit",
        source_checksum="5" * 64,
        device_seeds={"d1": "priority-d1"},
    )

    assignment = _assignment_for(repository, priority.priority_round_id, "d1")
    assert assignment.phase is AssignmentPhase.PENDING
    assert assignment.visit_confirmed_at_ms is None


def test_ordinary_completion_satisfies_priority_created_during_current_lease(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "leased.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "6" * 64, (_target("buyer.one"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    current = repository.claim_next_assignment(
        ordinary, "d1", "ordinary-worker", now_ms=700
    )
    assert current is not None
    priority_pool = repository.import_pool(
        "live.jsonl", "7" * 64, (_target("buyer.one"),)
    )
    priority = repository.create_priority_batch(
        batch_id="priority-during-lease",
        parent_round_id=ordinary,
        pool_id=priority_pool.pool_id,
        source_live_id="live-during-lease",
        source_checksum="7" * 64,
        device_seeds={"d1": "priority-d1"},
    )

    _complete_confirmed(
        repository, current.assignment_id, "ordinary-worker", now_ms=800
    )

    duplicate = _assignment_for(repository, priority.priority_round_id, "d1")
    assert duplicate.phase is AssignmentPhase.COMPLETED
    assert duplicate.visit_confirmed_at_ms == 800
    assert repository.assignment_phase_history(duplicate.assignment_id)[-1].details == {
        "reason": "satisfied_by_parent",
        "source_assignment_id": current.assignment_id,
    }


def test_priority_completion_satisfies_matching_later_fifo_batch(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(
        tmp_path / "fifo-dedupe.db", clock_ms=lambda: 500
    )
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "8" * 64, (_target("buyer.one"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    first_pool = repository.import_pool(
        "first.jsonl", "9" * 64, (_target("buyer.one"),)
    )
    first = repository.create_priority_batch(
        batch_id="priority-fifo-first",
        parent_round_id=ordinary,
        pool_id=first_pool.pool_id,
        source_live_id="live-first",
        source_checksum="9" * 64,
        device_seeds={"d1": "priority-first-d1"},
    )
    second_pool = repository.import_pool(
        "second.jsonl", "0" * 64, (_target("buyer.one"),)
    )
    second = repository.create_priority_batch(
        batch_id="priority-fifo-second",
        parent_round_id=ordinary,
        pool_id=second_pool.pool_id,
        source_live_id="live-second",
        source_checksum="0" * 64,
        device_seeds={"d1": "priority-second-d1"},
    )
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert assignment is not None
    assert assignment.round_id == first.priority_round_id

    _complete_confirmed(repository, assignment.assignment_id, "worker-1", now_ms=1_100)

    duplicate = _assignment_for(repository, second.priority_round_id, "d1")
    assert duplicate.phase is AssignmentPhase.COMPLETED
    assert duplicate.visit_confirmed_at_ms == 1_100
    assert repository.assignment_phase_history(duplicate.assignment_id)[-1].details == {
        "reason": "satisfied_by_priority",
        "source_assignment_id": assignment.assignment_id,
    }


def test_last_ordinary_completion_keeps_unmatched_priority_work_schedulable(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "remaining.db", clock_ms=lambda: 500)
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "a1" * 32, (_target("buyer.one"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    current = repository.claim_next_assignment(
        ordinary, "d1", "ordinary-worker", now_ms=700
    )
    assert current is not None
    priority_pool = repository.import_pool(
        "live.jsonl",
        "b2" * 32,
        (_target("buyer.one"), _target("buyer.two")),
    )
    priority = repository.create_priority_batch(
        batch_id="priority-with-remainder",
        parent_round_id=ordinary,
        pool_id=priority_pool.pool_id,
        source_live_id="live-with-remainder",
        source_checksum="b2" * 32,
        device_seeds={"d1": "priority-d1"},
    )

    _complete_confirmed(
        repository, current.assignment_id, "ordinary-worker", now_ms=800
    )
    remaining = repository.claim_scheduled_assignment(
        ordinary, "d1", "priority-worker", now_ms=900
    )

    assert remaining is not None
    assert remaining.round_id == priority.priority_round_id
    assert remaining.username == "buyer.two"


def test_last_ordinary_skip_keeps_priority_work_schedulable(tmp_path: Path) -> None:
    repository = AcquisitionRepository(
        tmp_path / "skipped-parent.db", clock_ms=lambda: 500
    )
    repository.migrate()
    ordinary_pool = repository.import_pool(
        "ordinary.csv", "c3" * 32, (_target("buyer.one"),)
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={"d1": "ordinary-d1"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    current = repository.claim_next_assignment(
        ordinary, "d1", "ordinary-worker", now_ms=700
    )
    assert current is not None
    priority_pool = repository.import_pool(
        "live.jsonl", "d4" * 32, (_target("buyer.two"),)
    )
    priority = repository.create_priority_batch(
        batch_id="priority-after-skip",
        parent_round_id=ordinary,
        pool_id=priority_pool.pool_id,
        source_live_id="live-after-skip",
        source_checksum="d4" * 32,
        device_seeds={"d1": "priority-d1"},
    )
    repository.skip_unreachable_assignment(
        current.assignment_id,
        "ordinary-worker",
        now_ms=800,
        error_code="profile_unreachable",
        original_error_code="profile_missing",
        failure_stage="route",
        diagnostics=DeviceDiagnostics(ui_summary="profile unavailable"),
    )

    remaining = repository.claim_scheduled_assignment(
        ordinary, "d1", "priority-worker", now_ms=900
    )

    assert remaining is not None
    assert remaining.round_id == priority.priority_round_id
