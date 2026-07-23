from pathlib import Path

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


def _handle_target(name: str) -> Target:
    return Target(
        target_id="",
        username=name,
        profile_url=f"https://www.tiktok.com/@{name}",
        source_video_id="",
        sec_uid="",
        identity_key=f"handle:{name.lower()}",
        source_line_numbers=(1,),
    )


def _complete(
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


def _attempts(repository: AcquisitionRepository, round_id: str) -> dict[int, int]:
    with repository._connect() as connection:
        rows = connection.execute(
            """
            SELECT assignment_id, attempt_count FROM round_assignments
            WHERE round_id = ? ORDER BY assignment_id
            """,
            (round_id,),
        ).fetchall()
    return {int(row["assignment_id"]): int(row["attempt_count"]) for row in rows}


def test_priority_fifo_and_ordinary_checkpoint_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "recovery.db"
    repository = AcquisitionRepository(database, clock_ms=lambda: 100)
    repository.migrate()
    devices = ("d1", "d2", "d3")
    ordinary_pool = repository.import_pool(
        "ordinary.csv",
        "a" * 64,
        (_handle_target("ordinary-one"), _handle_target("ordinary-two")),
    )
    ordinary = create_exposure_round(
        repository,
        pool_id=ordinary_pool.pool_id,
        device_seeds={device: f"ordinary-{device}" for device in devices},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    current = repository.claim_next_assignment(
        ordinary, "d1", "ordinary-worker", now_ms=200
    )
    assert current is not None
    shared_target = _target(current.username)
    priority_one_pool = repository.import_pool(
        "priority-one.jsonl",
        "b" * 64,
        (_target("priority-one"), shared_target),
    )
    priority_one = repository.create_priority_batch(
        batch_id="priority-one",
        parent_round_id=ordinary,
        pool_id=priority_one_pool.pool_id,
        source_live_id="live-one",
        source_checksum="b" * 64,
        device_seeds={device: f"priority-one-{device}" for device in devices},
    )
    priority_two_pool = repository.import_pool(
        "priority-two.jsonl",
        "c" * 64,
        (_target("priority-two"), shared_target),
    )
    priority_two = repository.create_priority_batch(
        batch_id="priority-two",
        parent_round_id=ordinary,
        pool_id=priority_two_pool.pool_id,
        source_live_id="live-two",
        source_checksum="c" * 64,
        device_seeds={device: f"priority-two-{device}" for device in devices},
    )
    sequence = [("ordinary", current.device_id, current.assignment_id)]
    _complete(repository, current.assignment_id, "ordinary-worker", now_ms=300)

    first = repository.claim_scheduled_assignment(
        ordinary, "d1", "priority-worker-d1", now_ms=400
    )
    assert first is not None
    assert first.round_id == priority_one.priority_round_id
    sequence.append(("priority-one", first.device_id, first.assignment_id))
    _complete(repository, first.assignment_id, "priority-worker-d1", now_ms=500)
    expired = repository.claim_scheduled_assignment(
        ordinary,
        "d2",
        "priority-worker-d2",
        now_ms=600,
        lease_ttl_ms=10,
    )
    assert expired is not None
    assert expired.round_id == priority_one.priority_round_id
    sequence.append(("priority-one", expired.device_id, expired.assignment_id))
    ordinary_attempts_before_restart = _attempts(repository, ordinary)

    reopened = AcquisitionRepository(database, clock_ms=lambda: 1_000)
    reopened.migrate()
    assert _attempts(reopened, ordinary) == ordinary_attempts_before_restart
    assert reopened.recover_expired_assignment_leases(now_ms=1_000) == 1

    now_ms = 1_100
    for _ in range(30):
        made_progress = False
        for device in devices:
            owner = f"recovered-{device}"
            assignment = reopened.claim_scheduled_assignment(
                ordinary, device, owner, now_ms=now_ms
            )
            now_ms += 10
            if assignment is None:
                continue
            made_progress = True
            if assignment.round_id == priority_one.priority_round_id:
                label = "priority-one"
            elif assignment.round_id == priority_two.priority_round_id:
                label = "priority-two"
                assert _attempts(reopened, ordinary) == ordinary_attempts_before_restart
            else:
                label = "ordinary"
            sequence.append((label, assignment.device_id, assignment.assignment_id))
            _complete(reopened, assignment.assignment_id, owner, now_ms=now_ms)
            now_ms += 10
        if reopened.round_operationally_complete(ordinary):
            break
        assert made_progress
    else:
        raise AssertionError("recovered priority queue did not finish")

    labels = [label for label, _device, _assignment in sequence]
    assert labels[0] == "ordinary"
    tail = labels[1:]
    priority_one_indexes = [
        index for index, label in enumerate(tail) if label == "priority-one"
    ]
    priority_two_indexes = [
        index for index, label in enumerate(tail) if label == "priority-two"
    ]
    ordinary_indexes = [
        index for index, label in enumerate(tail) if label == "ordinary"
    ]
    assert max(priority_one_indexes) < min(priority_two_indexes)
    assert max(priority_two_indexes) < min(ordinary_indexes)
    assert (
        reopened.round_completion(priority_one.priority_round_id).visits_confirmed == 6
    )
    assert (
        reopened.round_completion(priority_two.priority_round_id).visits_confirmed == 6
    )
    assert reopened.round_completion(ordinary).visits_confirmed == 6
    assert reopened.assignment(current.assignment_id).attempt_count == 1

    with reopened._connect() as connection:
        duplicate_confirmations = connection.execute(
            """
            SELECT assignment.identity_key, assignment.device_id,
                   COUNT(*) AS confirmations
            FROM assignment_phase_history AS history
            JOIN round_assignments AS assignment
              ON assignment.assignment_id = history.assignment_id
            WHERE history.to_phase = 'identity_confirmed'
            GROUP BY assignment.identity_key, assignment.device_id
            HAVING confirmations > 1
            """
        ).fetchall()
        upgraded_alias_duplicates = connection.execute(
            """
            SELECT assignment.device_id, COUNT(*) AS confirmations
            FROM assignment_phase_history AS history
            JOIN round_assignments AS assignment
              ON assignment.assignment_id = history.assignment_id
            JOIN exposure_rounds AS round
              ON round.round_id = assignment.round_id
            JOIN pool_targets AS target
              ON target.pool_id = round.pool_id
             AND target.identity_key = assignment.identity_key
            WHERE history.to_phase = 'identity_confirmed'
              AND (
                    lower(target.username) = lower(?)
                 OR target.sec_uid = ?
                 OR target.target_id = ?
              )
            GROUP BY assignment.device_id HAVING confirmations > 1
            """,
            (shared_target.username, shared_target.sec_uid, shared_target.target_id),
        ).fetchall()
        ordinary_rows = connection.execute(
            """
            SELECT assignment.assignment_id, assignment.attempt_count,
                   SUM(history.to_phase = 'identity_confirmed') AS confirmations
            FROM round_assignments AS assignment
            LEFT JOIN assignment_phase_history AS history
              ON history.assignment_id = assignment.assignment_id
            WHERE assignment.round_id = ?
            GROUP BY assignment.assignment_id, assignment.attempt_count
            ORDER BY assignment.assignment_id
            """,
            (ordinary,),
        ).fetchall()
    assert duplicate_confirmations == []
    assert upgraded_alias_duplicates == []
    assert {
        int(row["assignment_id"]): int(row["attempt_count"]) for row in ordinary_rows
    } == {
        int(row["assignment_id"]): int(row["confirmations"] or 0)
        for row in ordinary_rows
    }
