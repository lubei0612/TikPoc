import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.capacity import (
    AssignmentTiming,
    evaluate_capacity,
    evaluate_round_capacity,
)
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def synthetic_completed_timings(
    timings_by_device: dict[str, list[int]],
) -> tuple[AssignmentTiming, ...]:
    rows: list[AssignmentTiming] = []
    assignment_id = 1
    for device_id, durations in timings_by_device.items():
        for index, duration_ms in enumerate(durations):
            rows.append(
                AssignmentTiming(
                    assignment_id=assignment_id,
                    identity_key=f"target-{index}",
                    device_id=device_id,
                    duration_ms=duration_ms,
                )
            )
            assignment_id += 1
    return tuple(rows)


def seven_fast_devices() -> dict[str, list[int]]:
    return {f"phone-{index:02d}": [6_000] * 10 for index in range(1, 8)}


def repository_with_round(
    tmp_path: Path, *, target_count: int = 2
) -> tuple[AcquisitionRepository, str]:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 500)
    repository.migrate()
    targets = tuple(
        Target(
            target_id=f"user-{index}",
            username=f"buyer_{index}",
            profile_url=f"https://www.tiktok.com/@buyer_{index}",
            source_video_id="video-1",
            sec_uid=f"sec-{index}",
            identity_key=f"sec:sec-{index}",
            source_line_numbers=(index + 2,),
        )
        for index in range(target_count)
    )
    pool = repository.import_pool("comments.csv", f"{target_count:064x}", targets)
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
    )
    return repository, round_id


def test_capacity_migration_indexes_assignment_phase_history(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()

    with sqlite3.connect(repository.path) as connection:
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(assignment_phase_history)")
        }

    assert "assignment_phase_history_capacity_idx" in indexes


def test_capacity_migration_indexes_quota_plan_lookup(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()

    with sqlite3.connect(repository.path) as connection:
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(device_action_plans)")
        }
        columns = tuple(
            str(row[2])
            for row in connection.execute(
                "PRAGMA index_info(device_action_plans_capacity_quota_idx)"
            )
        )

    assert "device_action_plans_capacity_quota_idx" in indexes
    assert columns == (
        "device_id",
        "effective_outcome",
        "quota_window_start_ms",
        "state",
    )


def seed_completed_round(repository: AcquisitionRepository, round_id: str) -> None:
    with sqlite3.connect(repository.path) as connection:
        assignments = connection.execute(
            """
            SELECT assignment.assignment_id, assignment.identity_key,
                   assignment.device_id, target.username
            FROM round_assignments AS assignment
            JOIN exposure_rounds AS round
              ON round.round_id = assignment.round_id
            JOIN pool_targets AS target
              ON target.pool_id = round.pool_id
             AND target.identity_key = assignment.identity_key
            WHERE assignment.round_id = ?
            ORDER BY assignment_id
            """,
            (round_id,),
        ).fetchall()
        for index, (
            assignment_id,
            identity_key,
            device_id,
            username,
        ) in enumerate(assignments):
            started_at_ms = 10_000 + index * 10_000
            duration_ms = 6_000 if device_id == "phone-01" else 6_400
            connection.execute(
                """
                UPDATE round_assignments
                SET phase = 'completed', visit_confirmed_at_ms = ?, completed_at_ms = ?
                WHERE assignment_id = ?
                """,
                (started_at_ms + 100, started_at_ms + duration_ms, assignment_id),
            )
            connection.execute(
                """
                INSERT INTO assignment_phase_history(
                    assignment_id, from_phase, to_phase, details_json, changed_at_ms
                ) VALUES (?, 'pending', 'profile_opening', '{}', ?)
                """,
                (assignment_id, started_at_ms),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO profile_snapshots(
                    round_id, identity_key, observed_by_device_id,
                    observed_username, following_count, followers_count,
                    post_count, private_account, access_state, eligible,
                    reason, observed_at_ms
                ) VALUES (?, ?, ?, ?, 10, 20, 5, 0, 'public', 0,
                          'following_not_greater_than_followers', ?)
                """,
                (
                    round_id,
                    identity_key,
                    device_id,
                    username,
                    started_at_ms + 150,
                ),
            )
            connection.execute(
                """
                INSERT INTO device_action_plans(
                    round_id, identity_key, device_id, seed,
                    requested_outcome, effective_outcome, quota_reason,
                    state, created_at_ms
                ) VALUES (?, ?, ?, ?, 'trace', 'trace', 'profile_ineligible',
                          'confirmed', ?)
                """,
                (
                    round_id,
                    identity_key,
                    device_id,
                    f"seed-{assignment_id}",
                    started_at_ms + 200,
                ),
            )
        connection.execute(
            "UPDATE exposure_rounds SET state = 'completed' WHERE round_id = ?",
            (round_id,),
        )


def seed_video_confirmations(connection: sqlite3.Connection, round_id: str) -> None:
    rows = connection.execute(
        """
        SELECT assignment.assignment_id, plan.created_at_ms,
               assignment.completed_at_ms
        FROM round_assignments AS assignment
        JOIN device_action_plans AS plan
          ON plan.round_id = assignment.round_id
         AND plan.identity_key = assignment.identity_key
         AND plan.device_id = assignment.device_id
        WHERE assignment.round_id = ?
        """,
        (round_id,),
    ).fetchall()
    for assignment_id, plan_created_at_ms, completed_at_ms in rows:
        confirmed_at_ms = int(plan_created_at_ms) + 100
        assert confirmed_at_ms <= int(completed_at_ms)
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase,
                details_json, changed_at_ms
            ) VALUES (?, 'video_opening', 'video_confirmed', '{}', ?)
            """,
            (assignment_id, confirmed_at_ms),
        )


def prepare_eligible_round(connection: sqlite3.Connection, round_id: str) -> None:
    connection.execute(
        """
        UPDATE profile_snapshots
        SET following_count = 20, followers_count = 10, post_count = 5,
            eligible = 1, reason = 'eligible'
        WHERE round_id = ?
        """,
        (round_id,),
    )
    connection.execute(
        """
        UPDATE device_action_plans
        SET requested_outcome = 'trace', effective_outcome = 'trace',
            quota_window_start_ms = NULL, quota_reason = NULL,
            video_key = 'video-1'
        WHERE round_id = ?
        """,
        (round_id,),
    )
    seed_video_confirmations(connection, round_id)


def seed_quota_plans(
    connection: sqlite3.Connection,
    round_id: str,
    *,
    outcome: str,
    count: int,
    confirmed_count: int,
) -> None:
    assert 0 <= confirmed_count <= count
    pool_id = str(
        connection.execute(
            "SELECT pool_id FROM exposure_rounds WHERE round_id = ?", (round_id,)
        ).fetchone()[0]
    )
    evidence_round_id = f"{round_id}-quota-history"
    connection.execute(
        """
        INSERT INTO exposure_rounds(
            round_id, pool_id, state, starts_at_ms,
            min_inter_device_gap_ms, min_repeat_gap_ms, created_at_ms
        ) VALUES (?, ?, 'completed', 0, 0, 0, 0)
        """,
        (evidence_round_id, pool_id),
    )
    for index in range(count):
        state = "confirmed" if index < confirmed_count else "planned"
        plan_id = int(
            connection.execute(
                """
                INSERT INTO device_action_plans(
                    round_id, identity_key, device_id, seed,
                    requested_outcome, effective_outcome,
                    quota_window_start_ms, quota_reason,
                    video_key, state, created_at_ms
                ) VALUES (?, ?, 'phone-01', ?, ?, ?, 0, NULL,
                          'video-evidence', ?, 10_000)
                RETURNING plan_id
                """,
                (
                    evidence_round_id,
                    f"quota-evidence:{outcome}:{index}",
                    f"quota-seed-{outcome}-{index}",
                    outcome,
                    outcome,
                    state,
                ),
            ).fetchone()[0]
        )
        if state == "confirmed":
            connection.execute(
                """
                INSERT INTO action_attempts(
                    plan_id, attempt_index, result,
                    diagnostics_json, attempted_at_ms
                ) VALUES (?, 1, 'confirmed', '{}', 11_000)
                """,
                (plan_id,),
            )


def test_capacity_uses_slowest_device_and_completed_assignments() -> None:
    rows = synthetic_completed_timings(
        {"phone-01": [6_000] * 100, "phone-02": [6_400] * 100}
    )

    report = evaluate_capacity(
        rows,
        expected_devices=2,
        target_count=10_000,
        effective_hours=20,
    )

    assert report.slowest_device_id == "phone-02"
    assert report.projected_unique_per_day == 11_250


def test_uncertain_or_missing_coverage_fails_promotion() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings(seven_fast_devices()),
        expected_devices=7,
        target_count=10_000,
        effective_hours=20,
        uncertain_count=1,
        fully_covered_targets=9_999,
    )

    assert report.passed is False
    assert "uncertain assignments" in report.reasons
    assert "7/7 coverage incomplete" in report.reasons


def test_capacity_passes_only_with_exact_fast_complete_coverage() -> None:
    rows = synthetic_completed_timings(
        {"phone-01": [6_000, 6_000], "phone-02": [6_400, 6_400]}
    )

    report = evaluate_capacity(
        rows,
        expected_devices=2,
        expected_device_ids=("phone-01", "phone-02"),
        target_count=2,
        effective_hours=20,
        total_assignment_count=4,
    )

    assert report.passed is True
    assert report.reasons == ()
    assert report.fully_covered_targets == 2
    assert report.measured_seconds == 12.8


def test_capacity_rejects_projection_below_target_despite_fast_complete_data() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings(
            {"phone-01": [3_600] * 10, "phone-02": [3_600] * 10}
        ),
        expected_devices=2,
        target_count=10,
        effective_hours=0.009,
    )

    assert report.projected_unique_per_day == 9
    assert report.passed is False
    assert "projected capacity below target" in report.reasons


def test_capacity_allows_projection_exactly_equal_to_target() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings(
            {"phone-01": [3_600] * 10, "phone-02": [3_600] * 10}
        ),
        expected_devices=2,
        target_count=10,
        effective_hours=0.01,
    )

    assert report.projected_unique_per_day == 10
    assert "projected capacity below target" not in report.reasons
    assert report.passed is True


def test_capacity_uses_overall_mean_budget_and_reports_p90_informationally() -> None:
    rows = synthetic_completed_timings(
        {
            "phone-01": [8_640] * 10,
            "phone-02": [7_000] * 8 + [12_000] * 2,
        }
    )

    report = evaluate_capacity(
        rows,
        expected_devices=2,
        target_count=10,
        effective_hours=24,
        fully_covered_targets=10,
        total_assignment_count=20,
    )

    assert report.devices["phone-01"].mean_ms == 8_640
    assert report.devices["phone-01"].passed is True
    assert report.devices["phone-02"].mean_ms == 8_000
    assert report.devices["phone-02"].p90_ms == 12_000
    assert report.devices["phone-02"].passed is True
    assert "device average timing threshold exceeded" not in report.reasons


def test_capacity_rejects_device_average_above_daily_budget() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings({"phone-01": [8_641] * 10}),
        expected_devices=1,
        target_count=10,
        effective_hours=24,
        fully_covered_targets=10,
        total_assignment_count=10,
    )

    assert report.devices["phone-01"].passed is False
    assert "device average timing threshold exceeded" in report.reasons


def test_capacity_requires_every_configured_device() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings({"phone-01": [6_000]}),
        expected_devices=2,
        expected_device_ids=("phone-01", "phone-02"),
        target_count=1,
        effective_hours=20,
        fully_covered_targets=0,
        total_assignment_count=2,
    )

    assert report.devices["phone-02"].confirmed == 0
    assert report.devices["phone-02"].projected_per_effective_day == 0
    assert report.slowest_device_id == "phone-02"
    assert report.projected_unique_per_day == 0
    assert "expected device count incomplete" in report.reasons


def test_capacity_rejects_integrity_and_recovery_anomalies() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings({"phone-01": [6_000], "phone-02": [6_000]}),
        expected_devices=2,
        target_count=1,
        effective_hours=20,
        fully_covered_targets=1,
        total_assignment_count=2,
        identity_mismatch_count=1,
        false_success_count=1,
        quota_overrun_count=1,
        deferred_count=1,
    )

    assert report.passed is False
    assert "identity mismatches" in report.reasons
    assert "false completed outcomes" in report.reasons
    assert "quota overruns" in report.reasons
    assert "pending deferred work" in report.reasons


def test_round_capacity_uses_only_structurally_completed_assignment_timings(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path)
    seed_completed_round(repository, round_id)

    audit = repository.capacity_audit(round_id, expected_devices=2)
    report = evaluate_round_capacity(
        repository,
        round_id,
        expected_devices=2,
        target_count=2,
        effective_hours=20,
    )

    assert [row.duration_ms for row in audit.timings] == [6_000, 6_400, 6_000, 6_400]
    assert audit.fully_covered_targets == 2
    assert audit.total_assignment_count == 4
    assert report.passed is True


def test_round_capacity_accepts_paced_trace_when_no_action_is_due(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        prepare_eligible_round(connection, round_id)
        connection.execute(
            """
            UPDATE device_action_plans
            SET requested_outcome='trace', effective_outcome='trace',
                quota_window_start_ms=NULL, quota_reason='pacing_not_due'
            WHERE round_id=?
            """,
            (round_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 2
    assert audit.false_success_count == 0
    assert audit.quota_overrun_count == 0


def test_round_capacity_uses_the_final_attempt_start_time(tmp_path: Path) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        assignment_id, completed_at_ms = connection.execute(
            """
            SELECT assignment_id, completed_at_ms
            FROM round_assignments
            WHERE round_id=? AND device_id='phone-01'
            """,
            (round_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'deferred', 'profile_opening', '{}', ?)
            """,
            (assignment_id, int(completed_at_ms) - 1_000),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert [timing.duration_ms for timing in audit.timings] == [1_000, 6_400]
    assert audit.false_success_count == 0


def test_round_capacity_audit_uses_one_explicit_read_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    statements: list[tuple[str, bool]] = []
    original_connect_read_only = repository._connect_read_only

    class TrackingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(self, statement: str, parameters=()):
            cursor = self.connection.execute(statement, parameters)
            statements.append(
                (
                    statement.strip().split(None, 1)[0].upper(),
                    self.connection.in_transaction,
                )
            )
            return cursor

    @contextmanager
    def connect_read_only():
        with original_connect_read_only() as connection:
            yield TrackingConnection(connection)

    monkeypatch.setattr(repository, "_connect_read_only", connect_read_only)

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 2
    assert statements
    assert statements[0] == ("BEGIN", True)
    assert all(
        in_transaction for keyword, in_transaction in statements if keyword == "SELECT"
    )


def test_round_capacity_rejects_completed_assignments_without_snapshot(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "DELETE FROM profile_snapshots WHERE round_id = ?", (round_id,)
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 2


def test_round_capacity_requires_video_evidence_for_eligible_trace(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots
            SET following_count = 20, followers_count = 10, post_count = 5,
                eligible = 1, reason = 'eligible'
            WHERE round_id = ?
            """,
            (round_id,),
        )
        connection.execute(
            """
            UPDATE device_action_plans SET quota_reason = NULL
            WHERE round_id = ?
            """,
            (round_id,),
        )
        seed_video_confirmations(connection, round_id)

    missing_video = repository.capacity_audit(round_id, expected_devices=2)
    assert missing_video.timings == ()
    assert missing_video.false_success_count == 2

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE device_action_plans SET video_key = 'video-1'
            WHERE round_id = ?
            """,
            (round_id,),
        )

    valid = repository.capacity_audit(round_id, expected_devices=2)
    assert len(valid.timings) == 2
    assert valid.false_success_count == 0


def test_round_capacity_recomputes_eligibility_from_raw_snapshot_metrics(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE profile_snapshots SET eligible = 1 WHERE round_id = ?",
            (round_id,),
        )
        connection.execute(
            """
            UPDATE device_action_plans SET video_key = 'video-1'
            WHERE round_id = ?
            """,
            (round_id,),
        )
        seed_video_confirmations(connection, round_id)

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 2


@pytest.mark.parametrize(
    ("private_account", "access_state", "reason"),
    (
        (0, "public", "following_not_greater_than_followers"),
        (1, "private", "private_account"),
    ),
)
def test_round_capacity_accepts_username_drift_for_stable_identity(
    tmp_path: Path,
    private_account: int,
    access_state: str,
    reason: str,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots
            SET observed_username = 'different_user', private_account = ?,
                access_state = ?, eligible = 0, reason = ?
            WHERE round_id = ?
            """,
            (private_account, access_state, reason, round_id),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 2
    assert audit.false_success_count == 0


@pytest.mark.parametrize(
    "snapshot_update",
    (
        "following_count = NULL",
        "followers_count = NULL",
        "post_count = NULL",
        "private_account = 1",
        "access_state = 'private'",
    ),
)
def test_round_capacity_rejects_incomplete_or_inconsistent_public_snapshot(
    tmp_path: Path, snapshot_update: str
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            f"UPDATE profile_snapshots SET {snapshot_update} WHERE round_id = ?",
            (round_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 2


def test_round_capacity_accepts_consistent_nonpublic_access_snapshot(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots
            SET observed_username = '', following_count = NULL,
                followers_count = NULL, post_count = NULL,
                private_account = 0, access_state = 'suspended',
                eligible = 0, reason = 'profile_suspended'
            WHERE round_id = ?
            """,
            (round_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 2
    assert audit.false_success_count == 0


def test_round_capacity_requires_video_confirmed_history_for_eligible_snapshot(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots
            SET following_count = 20, followers_count = 10, post_count = 5,
                eligible = 1, reason = 'eligible'
            WHERE round_id = ?
            """,
            (round_id,),
        )
        connection.execute(
            """
            UPDATE device_action_plans
            SET video_key = 'video-1', quota_reason = NULL
            WHERE round_id = ?
            """,
            (round_id,),
        )

    missing_history = repository.capacity_audit(round_id, expected_devices=2)
    assert missing_history.timings == ()
    assert missing_history.false_success_count == 2

    with sqlite3.connect(repository.path) as connection:
        seed_video_confirmations(connection, round_id)

    valid = repository.capacity_audit(round_id, expected_devices=2)
    assert len(valid.timings) == 2
    assert valid.false_success_count == 0


def test_round_capacity_requires_video_confirmation_from_opening_phase(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots
            SET following_count = 20, followers_count = 10, post_count = 5,
                eligible = 1, reason = 'eligible'
            WHERE round_id = ?
            """,
            (round_id,),
        )
        connection.execute(
            """
            UPDATE device_action_plans
            SET video_key = 'video-1', quota_reason = NULL
            WHERE round_id = ?
            """,
            (round_id,),
        )
        seed_video_confirmations(connection, round_id)
        connection.execute(
            """
            UPDATE assignment_phase_history
            SET from_phase = 'identity_confirmed'
            WHERE to_phase = 'video_confirmed'
            """
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 2


def test_round_capacity_requires_plan_after_confirmed_visit(tmp_path: Path) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE device_action_plans
            SET created_at_ms = (
                SELECT assignment.visit_confirmed_at_ms - 1
                FROM round_assignments AS assignment
                WHERE assignment.round_id = device_action_plans.round_id
                  AND assignment.identity_key = device_action_plans.identity_key
                  AND assignment.device_id = device_action_plans.device_id
            )
            WHERE round_id = ? AND device_id = 'phone-01'
            """,
            (round_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 1
    assert audit.false_success_count == 1


def test_round_capacity_requires_snapshot_observed_before_plan(tmp_path: Path) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots
            SET observed_at_ms = (
                SELECT MAX(created_at_ms) + 1
                FROM device_action_plans
                WHERE device_action_plans.round_id = profile_snapshots.round_id
                  AND device_action_plans.identity_key = profile_snapshots.identity_key
            )
            WHERE round_id = ?
            """,
            (round_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 2


def test_round_capacity_requires_snapshot_observer_assignment(tmp_path: Path) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE profile_snapshots SET observed_by_device_id = 'phone-unknown'
            WHERE round_id = ?
            """,
            (round_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 2


def test_round_capacity_rejects_interaction_for_ineligible_snapshot(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        plan_id = int(
            connection.execute(
                """
                UPDATE device_action_plans
                SET requested_outcome = 'like', effective_outcome = 'like',
                    quota_window_start_ms = 0, video_key = 'video-1'
                WHERE round_id = ? AND device_id = 'phone-01'
                RETURNING plan_id
                """,
                (round_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES ('phone-01', 'like', 0, 1, 1, 0)
            """
        )
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'confirmed', '{}', 15_900)
            """,
            (plan_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 1
    assert audit.false_success_count == 1


def test_round_capacity_audits_uncertain_integrity_and_deferred_state(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    with sqlite3.connect(repository.path) as connection:
        rows = connection.execute(
            """
            SELECT assignment_id, identity_key, device_id
            FROM round_assignments WHERE round_id = ? ORDER BY device_id
            """,
            (round_id,),
        ).fetchall()
        false_assignment, deferred_assignment = rows
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'completed', completed_at_ms = 7_000
            WHERE assignment_id = ?
            """,
            (false_assignment[0],),
        )
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'pending', 'profile_opening', '{}', 1_000)
            """,
            (false_assignment[0],),
        )
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'deferred', last_error_code = 'identity_mismatch'
            WHERE assignment_id = ?
            """,
            (deferred_assignment[0],),
        )
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'profile_opening', 'deferred', ?, 2_000)
            """,
            (
                deferred_assignment[0],
                json.dumps({"error_code": "identity_mismatch"}),
            ),
        )
        connection.execute(
            """
            INSERT INTO device_action_plans(
                round_id, identity_key, device_id, seed,
                requested_outcome, effective_outcome, quota_window_start_ms,
                state, created_at_ms
            ) VALUES (?, ?, ?, 'seed', 'like', 'like', 0, 'uncertain', 2_000)
            """,
            (round_id, deferred_assignment[1], deferred_assignment[2]),
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES ('phone-02', 'like', 0, 101, 101, 0)
            """
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.uncertain_count == 1
    assert audit.identity_mismatch_count == 1
    assert audit.false_success_count == 1
    assert audit.quota_overrun_count == 1
    assert audit.deferred_count == 1
    assert audit.fully_covered_targets == 0


def test_round_capacity_requires_confirmed_evidence_for_interactions(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    with sqlite3.connect(repository.path) as connection:
        assignment_id, identity_key, device_id = connection.execute(
            """
            SELECT assignment_id, identity_key, device_id
            FROM round_assignments
            WHERE round_id = ? AND device_id = 'phone-01'
            """,
            (round_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'completed', visit_confirmed_at_ms = 1_100,
                completed_at_ms = 7_000
            WHERE assignment_id = ?
            """,
            (assignment_id,),
        )
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'pending', 'profile_opening', '{}', 1_000)
            """,
            (assignment_id,),
        )
        connection.execute(
            """
            INSERT INTO profile_snapshots(
                round_id, identity_key, observed_by_device_id,
                observed_username, following_count, followers_count,
                post_count, private_account, access_state, eligible,
                reason, observed_at_ms
            ) VALUES (?, ?, ?, 'buyer_0', 20, 10, 5, 0, 'public', 1,
                      'eligible', 1_150)
            """,
            (round_id, identity_key, device_id),
        )
        plan_id = int(
            connection.execute(
                """
                INSERT INTO device_action_plans(
                    round_id, identity_key, device_id, seed,
                    requested_outcome, effective_outcome, quota_window_start_ms,
                    video_key, state, created_at_ms
                ) VALUES (?, ?, ?, 'seed', 'like', 'like', 0, 'video-1',
                          'confirmed', 1_200)
                RETURNING plan_id
                """,
                (round_id, identity_key, device_id),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES (?, 'like', 0, 1, 1, 0)
            """,
            (device_id,),
        )
        seed_video_confirmations(connection, round_id)

    missing_evidence = repository.capacity_audit(round_id, expected_devices=2)
    assert missing_evidence.timings == ()
    assert missing_evidence.false_success_count == 1

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'confirmed', '{}', 7_001)
            """,
            (plan_id,),
        )

    late_evidence = repository.capacity_audit(round_id, expected_devices=2)
    assert late_evidence.timings == ()
    assert late_evidence.false_success_count == 1

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE action_attempts SET attempted_at_ms = 6_900 WHERE plan_id = ?",
            (plan_id,),
        )

    confirmed = repository.capacity_audit(round_id, expected_devices=2)
    assert len(confirmed.timings) == 1
    assert confirmed.false_success_count == 0


def test_round_capacity_rejects_a_nonpositive_recorded_duration(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    with sqlite3.connect(repository.path) as connection:
        assignment_id, identity_key, device_id = connection.execute(
            """
            SELECT assignment_id, identity_key, device_id
            FROM round_assignments
            WHERE round_id = ? AND device_id = 'phone-01'
            """,
            (round_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'completed', visit_confirmed_at_ms = 1_000,
                completed_at_ms = 1_000
            WHERE assignment_id = ?
            """,
            (assignment_id,),
        )
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'pending', 'profile_opening', '{}', 1_000)
            """,
            (assignment_id,),
        )
        connection.execute(
            """
            INSERT INTO device_action_plans(
                round_id, identity_key, device_id, seed,
                requested_outcome, effective_outcome, state, created_at_ms
            ) VALUES (?, ?, ?, 'seed', 'trace', 'trace', 'confirmed', 1_000)
            """,
            (round_id, identity_key, device_id),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert audit.timings == ()
    assert audit.false_success_count == 1


def test_round_capacity_rejects_interaction_without_quota_reservation(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        plan_id, device_id = connection.execute(
            """
            SELECT plan_id, device_id FROM device_action_plans
            WHERE round_id = ? AND device_id = 'phone-01'
            """,
            (round_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE device_action_plans
            SET requested_outcome = 'like', effective_outcome = 'like',
                video_key = 'video-1'
            WHERE plan_id = ?
            """,
            (plan_id,),
        )
        connection.execute(
            """
            UPDATE device_action_plans
            SET video_key = 'video-1', quota_reason = NULL
            WHERE round_id = ?
            """,
            (round_id,),
        )
        connection.execute(
            """
            UPDATE profile_snapshots
            SET following_count = 20, followers_count = 10, post_count = 5,
                eligible = 1, reason = 'eligible'
            WHERE round_id = ?
            """,
            (round_id,),
        )
        seed_video_confirmations(connection, round_id)
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'confirmed', '{}', 15_900)
            """,
            (plan_id,),
        )

    missing_quota = repository.capacity_audit(round_id, expected_devices=2)
    assert len(missing_quota.timings) == 1
    assert missing_quota.false_success_count == 1
    assert missing_quota.quota_overrun_count == 1

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE device_action_plans SET quota_window_start_ms = 0
            WHERE plan_id = ?
            """,
            (plan_id,),
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES (?, 'like', 0, 1, 1, 0)
            """,
            (device_id,),
        )

    reserved = repository.capacity_audit(round_id, expected_devices=2)
    assert len(reserved.timings) == 2
    assert reserved.false_success_count == 0
    assert reserved.quota_overrun_count == 0


def test_round_capacity_requires_plan_quota_window_to_match_created_hour(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        plan_id = int(
            connection.execute(
                """
                UPDATE device_action_plans
                SET requested_outcome = 'like', effective_outcome = 'like',
                    quota_window_start_ms = 3600000, video_key = 'video-1'
                WHERE round_id = ? AND device_id = 'phone-01'
                RETURNING plan_id
                """,
                (round_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE device_action_plans
            SET video_key = 'video-1', quota_reason = NULL
            WHERE round_id = ?
            """,
            (round_id,),
        )
        connection.execute(
            """
            UPDATE profile_snapshots
            SET following_count = 20, followers_count = 10, post_count = 5,
                eligible = 1, reason = 'eligible'
            WHERE round_id = ?
            """,
            (round_id,),
        )
        seed_video_confirmations(connection, round_id)
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'confirmed', '{}', 15_900)
            """,
            (plan_id,),
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES ('phone-01', 'like', 3600000, 1, 1, 0)
            """
        )

    mismatched = repository.capacity_audit(round_id, expected_devices=2)
    assert len(mismatched.timings) == 1
    assert mismatched.false_success_count == 1
    assert mismatched.quota_overrun_count == 1

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE device_action_plans SET quota_window_start_ms = 0
            WHERE plan_id = ?
            """,
            (plan_id,),
        )
        connection.execute(
            """
            UPDATE acquisition_quota_windows SET window_start_ms = 0
            WHERE device_id = 'phone-01' AND outcome = 'like'
              AND window_start_ms = 3600000
            """
        )

    corrected = repository.capacity_audit(round_id, expected_devices=2)
    assert len(corrected.timings) == 2
    assert corrected.false_success_count == 0
    assert corrected.quota_overrun_count == 0


def test_round_capacity_rejects_unknown_confirmed_outcome_plan(tmp_path: Path) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        prepare_eligible_round(connection, round_id)
        plan_id = int(
            connection.execute(
                """
                UPDATE device_action_plans
                SET requested_outcome = 'bogus', effective_outcome = 'bogus',
                    quota_window_start_ms = 0
                WHERE round_id = ? AND device_id = 'phone-01'
                RETURNING plan_id
                """,
                (round_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES ('phone-01', 'bogus', 0, 1, 1, 0)
            """
        )
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'confirmed', '{}', 15_900)
            """,
            (plan_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 1
    assert audit.false_success_count == 1
    assert audit.quota_overrun_count > 0


@pytest.mark.parametrize(
    (
        "eligible",
        "requested",
        "effective",
        "window_start_ms",
        "reason",
        "quota_outcome",
        "quota_count",
    ),
    (
        pytest.param(False, "like", "like", 0, None, "like", 1, id="ineligible-action"),
        pytest.param(
            False,
            "trace",
            "trace",
            0,
            "wrong_reason",
            None,
            0,
            id="ineligible-trace-metadata",
        ),
        pytest.param(
            True,
            "trace",
            "trace",
            0,
            "wrong_reason",
            None,
            0,
            id="eligible-direct-trace-metadata",
        ),
        pytest.param(
            True,
            "like",
            "favorite",
            0,
            None,
            "favorite",
            1,
            id="interaction-outcome-mismatch",
        ),
        pytest.param(
            True,
            "like",
            "like",
            0,
            "unexpected_reason",
            "like",
            1,
            id="interaction-reason",
        ),
        pytest.param(
            True,
            "like",
            "trace",
            0,
            "favorite_limit_reached",
            "like",
            100,
            id="fallback-reason",
        ),
        pytest.param(
            True,
            "bogus",
            "trace",
            0,
            "bogus_limit_reached",
            "bogus",
            100,
            id="fallback-unknown-request",
        ),
    ),
)
def test_round_capacity_rejects_invalid_outcome_plan_relationships(
    tmp_path: Path,
    eligible: bool,
    requested: str,
    effective: str,
    window_start_ms: int | None,
    reason: str | None,
    quota_outcome: str | None,
    quota_count: int,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        if eligible:
            prepare_eligible_round(connection, round_id)
        plan_id = int(
            connection.execute(
                """
                UPDATE device_action_plans
                SET requested_outcome = ?, effective_outcome = ?,
                    quota_window_start_ms = ?, quota_reason = ?
                WHERE round_id = ? AND device_id = 'phone-01'
                RETURNING plan_id
                """,
                (requested, effective, window_start_ms, reason, round_id),
            ).fetchone()[0]
        )
        if quota_outcome is not None:
            connection.execute(
                """
                INSERT INTO acquisition_quota_windows(
                    device_id, outcome, window_start_ms,
                    reserved_count, confirmed_count, uncertain_count
                ) VALUES ('phone-01', ?, 0, ?, ?, 0)
                """,
                (quota_outcome, quota_count, quota_count),
            )
        if effective != "trace":
            connection.execute(
                """
                INSERT INTO action_attempts(
                    plan_id, attempt_index, result,
                    diagnostics_json, attempted_at_ms
                ) VALUES (?, 1, 'confirmed', '{}', 15_900)
                """,
                (plan_id,),
            )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 1
    assert audit.false_success_count == 1
    assert audit.quota_overrun_count > 0


def test_round_capacity_accepts_fallback_when_reservations_reach_limit(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        prepare_eligible_round(connection, round_id)
        seed_quota_plans(
            connection,
            round_id,
            outcome="like",
            count=100,
            confirmed_count=99,
        )
        connection.execute(
            """
            UPDATE device_action_plans
            SET requested_outcome = 'like', effective_outcome = 'trace',
                quota_window_start_ms = 0,
                quota_reason = 'like_limit_reached'
            WHERE round_id = ? AND device_id = 'phone-01'
            """,
            (round_id,),
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES ('phone-01', 'like', 0, 100, 99, 0)
            """
        )

    exhausted = repository.capacity_audit(round_id, expected_devices=2)
    assert len(exhausted.timings) == 2
    assert exhausted.false_success_count == 0
    assert exhausted.quota_overrun_count == 0

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            UPDATE acquisition_quota_windows
            SET reserved_count = 99, confirmed_count = 99
            WHERE device_id = 'phone-01' AND outcome = 'like'
              AND window_start_ms = 0
            """
        )

    not_exhausted = repository.capacity_audit(round_id, expected_devices=2)
    assert len(not_exhausted.timings) == 1
    assert not_exhausted.false_success_count == 1
    assert not_exhausted.quota_overrun_count > 0


def test_round_capacity_accepts_visible_unavailable_action_trace_fallback(
    tmp_path: Path,
) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=1)
    seed_completed_round(repository, round_id)
    with sqlite3.connect(repository.path) as connection:
        prepare_eligible_round(connection, round_id)
        plan_id = int(
            connection.execute(
                """
            UPDATE device_action_plans
            SET requested_outcome = 'repost', effective_outcome = 'trace',
                quota_window_start_ms = NULL,
                quota_reason = 'repost_unavailable'
            WHERE round_id = ? AND device_id = 'phone-01'
            RETURNING plan_id
            """,
                (round_id,),
            ).fetchone()[0]
        )

    missing_evidence = repository.capacity_audit(round_id, expected_devices=2)
    assert len(missing_evidence.timings) == 1
    assert missing_evidence.false_success_count == 1

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'unavailable', '{}', 15_900)
            """,
            (plan_id,),
        )

    audit = repository.capacity_audit(round_id, expected_devices=2)

    assert len(audit.timings) == 2
    assert audit.false_success_count == 0
    assert audit.quota_overrun_count == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"expected_devices": 0}, "expected device count"),
        ({"target_count": 0}, "target count"),
        ({"effective_hours": 0}, "effective hours"),
        ({"uncertain_count": -1}, "audit counts"),
        ({"fully_covered_targets": 2}, "fully covered targets"),
        ({"total_assignment_count": -1}, "total assignment count"),
    ],
)
def test_capacity_rejects_invalid_contract_inputs(
    overrides: dict[str, int], message: str
) -> None:
    arguments = {
        "expected_devices": 1,
        "target_count": 1,
        "effective_hours": 20,
        "fully_covered_targets": 1,
        "total_assignment_count": 1,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        evaluate_capacity(
            synthetic_completed_timings({"phone-01": [6_000]}),
            **arguments,
        )


@pytest.mark.parametrize(
    "timing",
    [
        AssignmentTiming(0, "target-1", "phone-01", 6_000),
        AssignmentTiming(1, "", "phone-01", 6_000),
        AssignmentTiming(1, "target-1", "", 6_000),
        AssignmentTiming(1, "target-1", "phone-01", 0),
    ],
)
def test_capacity_rejects_invalid_completion_timings(
    timing: AssignmentTiming,
) -> None:
    with pytest.raises(ValueError, match="completed assignment timing"):
        evaluate_capacity(
            (timing,),
            expected_devices=1,
            target_count=1,
            effective_hours=20,
        )
