import sqlite3
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.device_api import MobileTaskResult
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def repository(tmp_path: Path) -> AcquisitionRepository:
    tokens = iter(("token-one", "token-two", "token-three"))
    result = AcquisitionRepository(
        tmp_path / "acquisition.db",
        clock_ms=lambda: 0,
        token_factory=tokens.__next__,
    )
    result.migrate()
    return result


def test_register_device_rotates_session_epoch_and_revokes_old_token(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)

    first = repo.register_mobile_device("device-1", "account-1", now_ms=1_000)
    second = repo.register_mobile_device("device-1", "account-1", now_ms=2_000)

    assert (first.session_epoch, first.access_token) == (1, "token-one")
    assert (second.session_epoch, second.access_token) == (2, "token-two")
    assert (
        repo.authenticate_mobile_device("device-1", "token-one", now_ms=3_000) is None
    )
    authenticated = repo.authenticate_mobile_device(
        "device-1", "token-two", now_ms=3_000
    )
    assert authenticated is not None
    assert authenticated.device_id == "device-1"
    assert authenticated.account_id == "account-1"
    assert authenticated.session_epoch == 2
    assert authenticated.access_token == ""


def test_registration_persists_only_token_digest(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    session = repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    with sqlite3.connect(repo.path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM mobile_devices WHERE device_id = 'device-1'"
        ).fetchone()

    assert row is not None
    assert "access_token" not in row
    assert row["token_digest"] != session.access_token
    assert len(row["token_digest"]) == 64


def test_register_rejects_account_binding_mismatch(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    with pytest.raises(ValueError, match="mobile device binding mismatch"):
        repo.register_mobile_device("device-1", "account-2", now_ms=2_000)


def test_revoked_device_does_not_authenticate(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    session = repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    repo.revoke_mobile_device("device-1", now_ms=2_000)

    assert (
        repo.authenticate_mobile_device("device-1", session.access_token, now_ms=3_000)
        is None
    )


def test_mobile_claim_is_bounded_and_result_upload_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    target = Target(
        target_id="target-1",
        username="target_user",
        profile_url="https://www.tiktok.com/@target_user",
        source_video_id="video-1",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )
    pool = repo.import_pool("targets.csv", "b" * 64, (target,))
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    tasks = repo.claim_mobile_tasks(
        round_id,
        "device-1",
        session_epoch=1,
        limit=50,
        now_ms=2_000,
    )

    assert len(tasks) == 1
    task = tasks[0]
    result = MobileTaskResult(
        device_id="device-1",
        session_epoch=1,
        task_id=task.task_id,
        lease_id=task.lease_id,
        idempotency_key="result-1",
        state="completed",
        phase="identity_confirmed",
        evidence={
            "observed_username": "target_user",
            "access_state": "available",
            "following": 10,
            "followers": 2,
            "video_count": 4,
            "post_handles": ["video-1", "video-2"],
        },
    )
    assert repo.record_mobile_result(result, now_ms=3_000) == "accepted"
    assert repo.record_mobile_result(result, now_ms=4_000) == "duplicate"
    assignment = repo.assignment(task.assignment_id)
    assert assignment.visit_confirmed_at_ms == 3_000
    snapshot = repo.profile_snapshot(round_id, assignment.identity_key)
    assert snapshot is not None
    assert snapshot.observed_username == "target_user"
    plan = repo.action_plan(round_id, assignment.identity_key, "device-1")
    assert plan is not None
    if plan.effective_outcome.value != "trace":
        assert plan.video_key in {"video-1", "video-2"}
    continuation = repo.claim_mobile_tasks(
        round_id,
        "device-1",
        session_epoch=1,
        limit=20,
        now_ms=4_000,
    )
    if plan.effective_outcome.value == "trace":
        assert continuation == ()
        assert repo.assignment(task.assignment_id).phase.value == "completed"
    else:
        assert len(continuation) == 1
        assert continuation[0].plan_id == plan.plan_id
        assert continuation[0].action == plan.effective_outcome.value
        assert continuation[0].video_key == plan.video_key
        action = MobileTaskResult(
            device_id="device-1",
            session_epoch=1,
            task_id=task.task_id,
            lease_id=task.lease_id,
            idempotency_key="action-1",
            state="completed",
            phase="action_executing",
            evidence={"plan_id": plan.plan_id},
        )
        assert repo.record_mobile_result(action, now_ms=5_000) == "accepted"
        assert repo.assignment(task.assignment_id).phase.value == "completed"
        assert repo.action_plan_by_id(plan.plan_id).state.value == "confirmed"


def test_mobile_task_envelope_carries_round_navigation_mode(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    target = Target(
        target_id="target-search",
        username="target_search",
        profile_url="https://www.tiktok.com/@target_search",
        source_video_id="video-1",
        sec_uid="sec-search",
        identity_key="sec:sec-search",
        source_line_numbers=(2,),
    )
    pool = repo.import_pool("search.jsonl", "e" * 64, (target,))
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
        navigation_mode="search",
    )
    repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    task = repo.claim_mobile_tasks(
        round_id,
        "device-1",
        session_epoch=1,
        limit=1,
        now_ms=2_000,
    )[0]

    assert task.navigation_mode == "search"


@pytest.mark.parametrize(
    ("state", "expected_phase"),
    (("deferred", "deferred"), ("skipped", "skipped")),
)
def test_profile_opening_result_releases_mobile_lease(
    tmp_path: Path, state: str, expected_phase: str
) -> None:
    repo = repository(tmp_path)
    target = Target(
        target_id="target-search",
        username="target_search",
        profile_url="https://www.tiktok.com/@target_search",
        source_video_id="video-1",
        sec_uid="sec-search",
        identity_key="sec:sec-search",
        source_line_numbers=(2,),
    )
    pool = repo.import_pool("search.jsonl", "f" * 64, (target,))
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
        navigation_mode="search",
    )
    repo.register_mobile_device("device-1", "account-1", now_ms=1_000)
    task = repo.claim_mobile_tasks(
        round_id, "device-1", session_epoch=1, limit=1, now_ms=2_000
    )[0]

    result = MobileTaskResult(
        device_id="device-1",
        session_epoch=1,
        task_id=task.task_id,
        lease_id=task.lease_id,
        idempotency_key=f"profile-{state}",
        state=state,
        phase="profile_opening",
        evidence={"code": "search_no_exact_match"},
    )

    assert repo.record_mobile_result(result, now_ms=3_000) == "accepted"
    assignment = repo.assignment(task.assignment_id)
    assert assignment.phase.value == expected_phase
    assert assignment.lease_owner is None
    assert assignment.last_error_code == "search_no_exact_match"
