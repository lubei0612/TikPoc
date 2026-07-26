import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import (
    ActionPlanState,
    ActionResult,
    AssignmentPhase,
    AssignmentStage,
    DeviceDiagnostics,
    OutcomeKind,
    ProfileAccessState,
)
from tikpoc.fleet import DeviceWorkerLeaseLost
from tikpoc.importer import Target
from tikpoc.models import ProfileMetrics
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


def _prepare_action_plan_subject(
    repository: AcquisitionRepository,
    *,
    metrics: ProfileMetrics | None = None,
) -> tuple[str, str]:
    if metrics is None:
        metrics = ProfileMetrics(following=20, followers=10, posts=1)
    imported = repository.import_pool("comments.csv", "e" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    repository.record_visit_confirmed(
        assignment.assignment_id, "worker-01", now_ms=1_000
    )
    assert repository.claim_snapshot_lease(
        round_id,
        assignment.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=100,
    )
    repository.publish_profile_snapshot(
        round_id,
        assignment.identity_key,
        device_id="phone-01",
        observed_username="buyer_s1",
        metrics=metrics,
        private_account=False,
        observed_at_ms=1_000,
    )
    return round_id, assignment.identity_key


@pytest.mark.parametrize(
    ("seed", "expected"),
    (
        ("mobile:assignment-5", OutcomeKind.LIKE),
        ("mobile:assignment-0", OutcomeKind.FAVORITE),
        ("mobile:assignment-1", OutcomeKind.REPOST),
        ("mobile:assignment-3", OutcomeKind.TRACE),
    ),
)
def test_paced_plan_selects_each_outcome_uniformly_from_full_mobile_seed(
    tmp_path: Path, seed: str, expected: OutcomeKind
) -> None:
    repository = AcquisitionRepository(
        tmp_path / f"{expected.value}.db", clock_ms=lambda: 1_000
    )
    repository.migrate()
    round_id, identity_key = _prepare_action_plan_subject(repository)

    plan = repository.create_paced_action_plan(
        round_id=round_id,
        identity_key=identity_key,
        device_id="phone-01",
        seed=seed,
        now_ms=1_000,
        hourly_limits={
            OutcomeKind.LIKE: 100,
            OutcomeKind.FAVORITE: 14,
            OutcomeKind.REPOST: 25,
        },
    )

    assert plan.requested_outcome is expected
    assert plan.effective_outcome is expected
    assert plan.quota_reason is None
    quota = repository.quota_window("phone-01", expected, 0)
    if expected is OutcomeKind.TRACE:
        assert quota is None
        assert plan.quota_window_start_ms is None
    else:
        assert quota is not None and quota.reserved_count == 1
        assert plan.quota_window_start_ms == 0


def test_paced_plan_preserves_selected_action_when_quota_is_exhausted(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    round_id, identity_key = _prepare_action_plan_subject(repository)

    plan = repository.create_paced_action_plan(
        round_id=round_id,
        identity_key=identity_key,
        device_id="phone-01",
        seed="mobile:assignment-0",
        now_ms=1_000,
        hourly_limits={
            OutcomeKind.LIKE: 100,
            OutcomeKind.FAVORITE: 0,
            OutcomeKind.REPOST: 25,
        },
    )

    assert plan.requested_outcome is OutcomeKind.FAVORITE
    assert plan.effective_outcome is OutcomeKind.TRACE
    assert plan.quota_reason == "favorite_limit_reached"
    assert plan.quota_window_start_ms == 0


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


def test_claim_selects_pending_assignment_before_due_deferred_assignment(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool(
        "comments.csv", "9" * 64, (_target("sec:s1"), _target("sec:s2"))
    )
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    first = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert first is not None
    repository.defer_assignment(
        first.assignment_id,
        "worker-01",
        now_ms=1_001,
        retry_delay_ms=0,
        error_code="temporary",
        diagnostics=DeviceDiagnostics(),
    )

    claimed = repository.claim_next_assignment(
        round_id, "phone-01", "worker-02", now_ms=1_002
    )

    assert claimed is not None
    assert claimed.assignment_id != first.assignment_id
    assert claimed.identity_key != first.identity_key


def test_stale_device_fence_cannot_claim_assignment(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_101)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "1" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.claim_next_assignment(
            round_id,
            "phone-01",
            "worker-01",
            now_ms=1_050,
            worker_account_id="account-01",
            worker_fence_token=token,
        )

    completion = repository.round_completion(round_id)
    assert completion.total == 1
    assert completion.visits_confirmed == 0
    assert completion.completed == 0
    assert completion.deferred == 0


def test_fenced_claim_uses_transaction_time_for_new_assignment_lease(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_101)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "a" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=1_000
    )
    assert isinstance(token, int)

    assignment = repository.claim_next_assignment(
        round_id,
        "phone-01",
        "worker-01",
        now_ms=1_050,
        lease_ttl_ms=100,
        worker_account_id="account-01",
        worker_fence_token=token,
    )

    assert assignment is not None
    assert assignment.lease_expires_at_ms == 1_201


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


@pytest.mark.parametrize("terminal_write", ["complete", "defer", "skip"])
def test_stale_device_fence_blocks_assignment_terminal_writes(
    tmp_path: Path,
    terminal_write: str,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "f" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    first_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(first_token, int)
    with sqlite3.connect(repository.path) as connection:
        if terminal_write == "complete":
            connection.execute(
                "UPDATE round_assignments SET phase='identity_confirmed' "
                "WHERE assignment_id=?",
                (assignment.assignment_id,),
            )
        elif terminal_write == "skip":
            connection.execute(
                "UPDATE round_assignments SET attempt_count=3 WHERE assignment_id=?",
                (assignment.assignment_id,),
            )
    replacement = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )
    assert isinstance(replacement, int) and replacement > first_token

    with pytest.raises(DeviceWorkerLeaseLost):
        if terminal_write == "complete":
            repository.complete_assignment(
                assignment.assignment_id,
                "worker-01",
                AssignmentPhase.IDENTITY_CONFIRMED,
                now_ms=1_101,
                worker_account_id="account-01",
                worker_fence_token=first_token,
            )
        elif terminal_write == "defer":
            repository.defer_assignment(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                retry_delay_ms=100,
                error_code="stale_worker",
                diagnostics=DeviceDiagnostics(),
                worker_account_id="account-01",
                worker_fence_token=first_token,
            )
        else:
            repository.skip_unreachable_assignment(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                error_code="profile_unreachable",
                original_error_code="ValueError",
                failure_stage="route",
                diagnostics=DeviceDiagnostics(),
                worker_account_id="account-01",
                worker_fence_token=first_token,
            )

    stored = repository.assignment(assignment.assignment_id)
    assert stored.lease_owner == "worker-01"
    assert stored.phase is (
        AssignmentPhase.IDENTITY_CONFIRMED
        if terminal_write == "complete"
        else AssignmentPhase.PROFILE_OPENING
    )


def test_stale_device_fence_cannot_record_confirmed_visit_coverage(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "5" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.record_visit_confirmed(
            assignment.assignment_id,
            "worker-01",
            now_ms=1_101,
            worker_account_id="account-01",
            worker_fence_token=token,
        )

    stored = repository.assignment(assignment.assignment_id)
    assert stored.visit_confirmed_at_ms is None
    assert stored.phase is AssignmentPhase.PROFILE_OPENING
    assert repository.round_coverage(round_id)["confirmed_visits"] == 0


def test_stale_device_fence_cannot_record_action_or_mutate_quota(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "9" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    repository.record_visit_confirmed(
        assignment.assignment_id, "worker-01", now_ms=1_000
    )
    assert repository.claim_snapshot_lease(
        round_id,
        assignment.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=100,
    )
    repository.publish_profile_snapshot(
        round_id,
        assignment.identity_key,
        device_id="phone-01",
        observed_username="buyer_s1",
        metrics=ProfileMetrics(following=20, followers=10, posts=1),
        private_account=False,
        access_state=ProfileAccessState.PUBLIC,
        observed_at_ms=1_000,
    )
    plan = repository.create_action_plan(
        round_id=round_id,
        identity_key=assignment.identity_key,
        device_id="phone-01",
        seed="a" * 64,
        requested_outcome=OutcomeKind.LIKE,
        now_ms=1_000,
        hourly_limits={OutcomeKind.LIKE: 100},
    )
    repository.mark_action_executing(plan.plan_id)
    first_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(first_token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.record_action_result(
            plan.plan_id,
            ActionResult.CONFIRMED,
            now_ms=1_101,
            worker_owner_id="worker-01",
            worker_account_id="account-01",
            worker_fence_token=first_token,
        )

    stored_plan = repository.action_plan_by_id(plan.plan_id)
    quota = repository.quota_window("phone-01", OutcomeKind.LIKE, 0)
    with sqlite3.connect(repository.path) as connection:
        attempts = connection.execute(
            "SELECT COUNT(*) FROM action_attempts WHERE plan_id=?", (plan.plan_id,)
        ).fetchone()[0]
    assert stored_plan.state is ActionPlanState.EXECUTING
    assert quota is not None
    assert quota.confirmed_count == 0
    assert quota.uncertain_count == 0
    assert attempts == 0

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE round_assignments SET lease_expires_at_ms=1200 "
            "WHERE assignment_id=?",
            (assignment.assignment_id,),
        )
    active_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_200, ttl_ms=100
    )
    assert isinstance(active_token, int)
    with pytest.raises(DeviceWorkerLeaseLost):
        repository.record_action_result(
            plan.plan_id,
            ActionResult.CONFIRMED,
            now_ms=1_201,
            worker_owner_id="worker-01",
            worker_account_id="account-01",
            worker_fence_token=active_token,
        )

    with sqlite3.connect(repository.path) as connection:
        attempts_after_expiry = connection.execute(
            "SELECT COUNT(*) FROM action_attempts WHERE plan_id=?", (plan.plan_id,)
        ).fetchone()[0]
    assert attempts_after_expiry == 0


def test_expired_assignment_lease_blocks_fenced_terminal_write(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "8" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id,
        "phone-01",
        "worker-01",
        now_ms=1_000,
        lease_ttl_ms=100,
    )
    assert assignment is not None
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=1_000
    )
    assert isinstance(token, int)

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.defer_assignment(
            assignment.assignment_id,
            "worker-01",
            now_ms=1_101,
            retry_delay_ms=100,
            error_code="late_result",
            diagnostics=DeviceDiagnostics(),
            worker_account_id="account-01",
            worker_fence_token=token,
        )

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.PROFILE_OPENING
    assert stored.lease_owner == "worker-01"


def test_replaced_assignment_owner_is_reported_as_worker_lease_loss(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "3" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000, lease_ttl_ms=100
    )
    assert assignment is not None
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=1_000
    )
    assert isinstance(token, int)
    replacement = repository.claim_next_assignment(
        round_id, "phone-01", "worker-02", now_ms=1_101, lease_ttl_ms=100
    )
    if replacement is None:
        replacement = repository.claim_next_assignment(
            round_id, "phone-01", "worker-02", now_ms=1_102, lease_ttl_ms=100
        )
    assert replacement is not None
    assert replacement.assignment_id == assignment.assignment_id

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.defer_assignment(
            assignment.assignment_id,
            "worker-01",
            now_ms=1_103,
            retry_delay_ms=100,
            error_code="late_route_result",
            diagnostics=DeviceDiagnostics(),
            worker_account_id="account-01",
            worker_fence_token=token,
        )


def test_transaction_fence_uses_current_repository_time(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_101)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "3" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000, lease_ttl_ms=1_000
    )
    assert assignment is not None
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.defer_assignment(
            assignment.assignment_id,
            "worker-01",
            now_ms=1_050,
            retry_delay_ms=100,
            error_code="late_transaction",
            diagnostics=DeviceDiagnostics(),
            worker_account_id="account-01",
            worker_fence_token=token,
        )


@pytest.mark.parametrize("write_kind", ["renew", "transition", "timing"])
def test_stale_device_fence_blocks_nonterminal_assignment_writes(
    tmp_path: Path,
    write_kind: str,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "2" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        if write_kind == "renew":
            repository.renew_assignment_lease(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                ttl_ms=1_000,
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        elif write_kind == "transition":
            repository.transition_assignment(
                assignment.assignment_id,
                "worker-01",
                AssignmentPhase.PROFILE_OPENING,
                AssignmentPhase.IDENTITY_CONFIRMED,
                now_ms=1_101,
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        else:
            repository.record_assignment_stage_timing(
                assignment.assignment_id,
                "route",
                duration_ms=10,
                recorded_at_ms=1_101,
                worker_owner_id="worker-01",
                worker_account_id="account-01",
                worker_fence_token=token,
            )

    assert repository.assignment(assignment.assignment_id).phase is (
        AssignmentPhase.PROFILE_OPENING
    )
    assert repository.assignment_stage_timings(assignment.assignment_id) == ()


@pytest.mark.parametrize(
    "write_kind", ["visit", "renew", "transition", "defer", "skip"]
)
def test_replaced_assignment_owner_raises_device_worker_lease_lost(
    tmp_path: Path,
    write_kind: str,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_101)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "b" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE round_assignments SET lease_owner='worker-02', "
            "lease_expires_at_ms=2000, attempt_count=3 WHERE assignment_id=?",
            (assignment.assignment_id,),
        )
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=1_000
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        if write_kind == "visit":
            repository.record_visit_confirmed(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        elif write_kind == "renew":
            repository.renew_assignment_lease(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                ttl_ms=100,
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        elif write_kind == "transition":
            repository.transition_assignment(
                assignment.assignment_id,
                "worker-01",
                AssignmentPhase.PROFILE_OPENING,
                AssignmentPhase.IDENTITY_CONFIRMED,
                now_ms=1_101,
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        elif write_kind == "defer":
            repository.defer_assignment(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                retry_delay_ms=100,
                error_code="stale",
                diagnostics=DeviceDiagnostics(),
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        else:
            repository.skip_unreachable_assignment(
                assignment.assignment_id,
                "worker-01",
                now_ms=1_101,
                error_code="profile_unreachable",
                original_error_code="ValueError",
                failure_stage="route",
                diagnostics=DeviceDiagnostics(),
                worker_account_id="account-01",
                worker_fence_token=token,
            )

    assert repository.assignment(assignment.assignment_id).lease_owner == "worker-02"


def test_stale_device_fence_cannot_release_unavailable_action_quota(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "7" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    repository.record_visit_confirmed(
        assignment.assignment_id, "worker-01", now_ms=1_000
    )
    assert repository.claim_snapshot_lease(
        round_id,
        assignment.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=100,
    )
    repository.publish_profile_snapshot(
        round_id,
        assignment.identity_key,
        device_id="phone-01",
        observed_username="buyer_s1",
        metrics=ProfileMetrics(following=20, followers=10, posts=1),
        private_account=False,
        observed_at_ms=1_000,
    )
    plan = repository.create_action_plan(
        round_id=round_id,
        identity_key=assignment.identity_key,
        device_id="phone-01",
        seed="b" * 64,
        requested_outcome=OutcomeKind.REPOST,
        now_ms=1_000,
        hourly_limits={OutcomeKind.REPOST: 25},
    )
    repository.mark_action_executing(plan.plan_id)
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    repository.record_action_result(
        plan.plan_id,
        ActionResult.UNAVAILABLE,
        now_ms=1_050,
        worker_owner_id="worker-01",
        worker_account_id="account-01",
        worker_fence_token=token,
    )
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.confirm_action_unavailable_as_trace(
            plan.plan_id,
            now_ms=1_101,
            worker_owner_id="worker-01",
            worker_account_id="account-01",
            worker_fence_token=token,
        )

    stored_plan = repository.action_plan_by_id(plan.plan_id)
    quota = repository.quota_window("phone-01", OutcomeKind.REPOST, 0)
    assert stored_plan.effective_outcome is OutcomeKind.REPOST
    assert stored_plan.state is ActionPlanState.PLANNED
    assert quota is not None and quota.reserved_count == 1


@pytest.mark.parametrize("plan_write", ["executing", "trace_confirmed"])
def test_stale_device_fence_blocks_worker_plan_state_writes(
    tmp_path: Path,
    plan_write: str,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "6" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    repository.record_visit_confirmed(
        assignment.assignment_id, "worker-01", now_ms=1_000
    )
    assert repository.claim_snapshot_lease(
        round_id,
        assignment.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=100,
    )
    repository.publish_profile_snapshot(
        round_id,
        assignment.identity_key,
        device_id="phone-01",
        observed_username="buyer_s1",
        metrics=ProfileMetrics(following=20, followers=10, posts=1),
        private_account=False,
        observed_at_ms=1_000,
    )
    plan = repository.create_action_plan(
        round_id=round_id,
        identity_key=assignment.identity_key,
        device_id="phone-01",
        seed="c" * 64,
        requested_outcome=(
            OutcomeKind.LIKE if plan_write == "executing" else OutcomeKind.TRACE
        ),
        now_ms=1_000,
        hourly_limits={OutcomeKind.LIKE: 100},
    )
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        if plan_write == "executing":
            repository.mark_action_executing(
                plan.plan_id,
                now_ms=1_101,
                worker_owner_id="worker-01",
                worker_account_id="account-01",
                worker_fence_token=token,
            )
        else:
            repository.confirm_trace_plan(
                plan.plan_id,
                now_ms=1_101,
                worker_owner_id="worker-01",
                worker_account_id="account-01",
                worker_fence_token=token,
            )

    assert repository.action_plan_by_id(plan.plan_id).state is ActionPlanState.PLANNED


@pytest.mark.parametrize("planner", ["fixed", "paced"])
def test_stale_device_fence_cannot_create_plan_or_reserve_quota(
    tmp_path: Path,
    planner: str,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "4" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    repository.record_visit_confirmed(
        assignment.assignment_id, "worker-01", now_ms=1_000
    )
    assert repository.claim_snapshot_lease(
        round_id,
        assignment.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=100,
    )
    repository.publish_profile_snapshot(
        round_id,
        assignment.identity_key,
        device_id="phone-01",
        observed_username="buyer_s1",
        metrics=ProfileMetrics(following=20, followers=10, posts=1),
        private_account=False,
        observed_at_ms=1_000,
    )
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )
    common = {
        "round_id": round_id,
        "identity_key": assignment.identity_key,
        "device_id": "phone-01",
        "seed": "d" * 64,
        "now_ms": 1_101,
        "worker_owner_id": "worker-01",
        "worker_account_id": "account-01",
        "worker_fence_token": token,
    }

    with pytest.raises(DeviceWorkerLeaseLost):
        if planner == "fixed":
            repository.create_action_plan(
                **common,
                requested_outcome=OutcomeKind.LIKE,
                hourly_limits={OutcomeKind.LIKE: 100},
            )
        else:
            repository.create_paced_action_plan(
                **common,
                hourly_limits={
                    OutcomeKind.LIKE: 100,
                    OutcomeKind.FAVORITE: 14,
                    OutcomeKind.REPOST: 25,
                },
            )

    assert repository.action_plan(round_id, assignment.identity_key, "phone-01") is None
    with sqlite3.connect(repository.path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM acquisition_quota_windows"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM action_pacing_state").fetchone()[0]
            == 0
        )


def test_stale_device_fence_cannot_publish_snapshot_or_set_plan_video(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    imported = repository.import_pool("comments.csv", "0" * 64, (_target("sec:s1"),))
    round_id = create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert assignment is not None
    repository.record_visit_confirmed(
        assignment.assignment_id, "worker-01", now_ms=1_000
    )
    assert repository.claim_snapshot_lease(
        round_id,
        assignment.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=1_000,
    )
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-01", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        repository.publish_profile_snapshot(
            round_id,
            assignment.identity_key,
            device_id="phone-01",
            observed_username="buyer_s1",
            metrics=ProfileMetrics(following=20, followers=10, posts=1),
            private_account=False,
            observed_at_ms=1_101,
            worker_owner_id="worker-01",
            worker_account_id="account-01",
            worker_fence_token=token,
        )
    assert repository.profile_snapshot(round_id, assignment.identity_key) is None

    repository.publish_profile_snapshot(
        round_id,
        assignment.identity_key,
        device_id="phone-01",
        observed_username="buyer_s1",
        metrics=ProfileMetrics(following=20, followers=10, posts=1),
        private_account=False,
        observed_at_ms=1_101,
    )
    plan = repository.create_action_plan(
        round_id=round_id,
        identity_key=assignment.identity_key,
        device_id="phone-01",
        seed="e" * 64,
        requested_outcome=OutcomeKind.LIKE,
        now_ms=1_101,
        hourly_limits={OutcomeKind.LIKE: 100},
    )
    with pytest.raises(DeviceWorkerLeaseLost):
        repository.set_plan_video(
            plan.plan_id,
            "video-1",
            now_ms=1_101,
            worker_owner_id="worker-01",
            worker_account_id="account-01",
            worker_fence_token=token,
        )
    assert repository.action_plan_by_id(plan.plan_id).video_key is None


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


def test_repository_round_trips_assignment_command_metrics(tmp_path: Path) -> None:
    repository = AcquisitionRepository(
        tmp_path / "command-metrics.db", clock_ms=lambda: 1_000
    )
    repository.migrate()
    pool = repository.import_pool("targets.csv", "f" * 64, (_target("sec:metrics"),))
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "metrics"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    assignment = repository.claim_scheduled_assignment(
        round_id,
        "phone-01",
        "worker-01",
        now_ms=1_000,
    )
    assert assignment is not None

    stored = repository.record_assignment_command_metrics(
        assignment.assignment_id,
        AssignmentStage.IDENTITY,
        command_count=3,
        command_duration_ms=620,
        page_source_reads=2,
        element_queries=1,
        execute_script_calls=0,
        helper_command_count=2,
        helper_processing_ms=31,
        host_round_trip_ms=44,
        tree_age_ms=7,
        event_wait_ms=18,
        fallback_count=1,
        fallback_reason="stale_tree",
        recorded_at_ms=1_010,
    )

    assert repository.assignment_command_metrics(assignment.assignment_id) == (stored,)


def test_migrate_adds_helper_metrics_to_prior_command_table(tmp_path: Path) -> None:
    path = tmp_path / "prior-command-metrics.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE assignment_command_metrics (
                assignment_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                command_count INTEGER NOT NULL DEFAULT 0,
                command_duration_ms INTEGER NOT NULL DEFAULT 0,
                page_source_reads INTEGER NOT NULL DEFAULT 0,
                element_queries INTEGER NOT NULL DEFAULT 0,
                execute_script_calls INTEGER NOT NULL DEFAULT 0,
                recorded_at_ms INTEGER NOT NULL,
                PRIMARY KEY(assignment_id, stage)
            )
            """
        )

    AcquisitionRepository(path).migrate()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(assignment_command_metrics)"
            ).fetchall()
        }
    assert {
        "helper_command_count",
        "helper_processing_ms",
        "host_round_trip_ms",
        "tree_age_ms",
        "event_wait_ms",
        "fallback_count",
        "fallback_reason",
    } <= columns
