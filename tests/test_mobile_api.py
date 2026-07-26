import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import ActionPlanState, AssignmentPhase, OutcomeKind
from tikpoc.api import create_app
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            tmp_path / "mobile.db",
            clock=lambda: 12.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )


def test_mobile_registration_requires_bootstrap_bearer(tmp_path: Path) -> None:
    api = client(tmp_path)
    payload = {"device_id": "device-1", "account_id": "account-1"}

    missing = api.post("/api/mobile/register", json=payload)
    registered = api.post(
        "/api/mobile/register",
        json=payload,
        headers={"Authorization": "Bearer bootstrap-secret"},
    )

    assert missing.status_code == 401
    assert registered.status_code == 200
    assert registered.json() == {
        "device_id": "device-1",
        "account_id": "account-1",
        "session_epoch": 1,
        "access_token": registered.json()["access_token"],
    }
    assert registered.json()["access_token"]


def test_mobile_heartbeat_authenticates_device_and_epoch(tmp_path: Path) -> None:
    api = client(tmp_path)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    payload = {
        "device_id": "device-1",
        "session_epoch": 1,
        "app_version": "1.0.0",
        "phase": "idle",
        "queue_depth": 0,
        "client_timestamp_ms": 11_000,
    }

    accepted = api.post("/api/mobile/heartbeat", json=payload, headers=headers)
    stale = api.post(
        "/api/mobile/heartbeat",
        json={**payload, "session_epoch": 2},
        headers=headers,
    )

    assert accepted.status_code == 200
    assert accepted.json() == {"accepted": True, "server_time_ms": 12_000}
    assert stale.status_code == 409
    assert stale.json() == {"error": "stale_session"}


def test_mobile_heartbeat_rejects_wrong_device_token(tmp_path: Path) -> None:
    api = client(tmp_path)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()

    response = api.post(
        "/api/mobile/heartbeat",
        json={
            "device_id": "device-2",
            "session_epoch": 1,
            "app_version": "1.0.0",
            "phase": "idle",
            "queue_depth": 0,
            "client_timestamp_ms": 11_000,
        },
        headers={"Authorization": f"Bearer {registered['access_token']}"},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_mobile_token"}


def test_mobile_pull_and_result_replay_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    pool = repo.import_pool(
        "targets.csv",
        "c" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 12.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    pulled = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 50,
        },
        headers=headers,
    )

    assert pulled.status_code == 200
    task = pulled.json()["tasks"][0]
    result = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_id": task["task_id"],
        "lease_id": task["lease_id"],
        "idempotency_key": "result-1",
        "state": "deferred",
        "phase": "profile_opening",
        "evidence": {"error_code": "network_lost"},
    }
    first = api.post("/api/mobile/results", json=result, headers=headers)
    replay = api.post("/api/mobile/results", json=result, headers=headers)

    assert first.json() == {"accepted": True, "state": "accepted"}
    assert replay.json() == {"accepted": False, "state": "duplicate"}


def test_mobile_profile_result_creates_video_bound_follow_up_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO sqlite_sequence(name, seq) "
            "VALUES ('round_assignments', 32809)"
        )
    pool = repo.import_pool(
        "targets.csv",
        "d" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 2_000_000_000.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    task = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]

    response = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "profile-1",
            "state": "completed",
            "phase": "identity_confirmed",
            "evidence": {
                "observed_username": "target_user",
                "access_state": "available",
                "following": 20,
                "followers": 10,
                "video_count": 2,
                "post_handles": ["post:0", "post:1"],
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    assignment = repo.assignment(int(task["task_id"]))
    plan = repo.action_plan(round_id, assignment.identity_key, "device-1")
    assert assignment.phase is AssignmentPhase.VIDEO_OPENING
    assert plan is not None and plan.video_key in {"post:0", "post:1"}
    assert plan.effective_outcome is OutcomeKind.FAVORITE
    assert plan.state is ActionPlanState.PLANNED
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE round_assignments SET lease_expires_at_ms=1 WHERE assignment_id=?",
            (int(task["task_id"]),),
        )

    follow_up = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"]
    assert len(follow_up) == 1
    assert follow_up[0]["task_id"] == task["task_id"]
    assert follow_up[0]["video_key"] == plan.video_key
    assert follow_up[0]["action"] == "favorite"
    assert repo.assignment(int(task["task_id"])).phase is AssignmentPhase.VIDEO_OPENING

    uncertain = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "action-uncertain-1",
            "state": "uncertain",
            "phase": "action_reconciling",
            "evidence": {"code": "action_unverified", "plan_id": plan.plan_id},
        },
        headers=headers,
    )

    assert uncertain.status_code == 200
    assert (
        repo.assignment(int(task["task_id"])).phase
        is AssignmentPhase.ACTION_RECONCILING
    )
    assert repo.action_plan_by_id(plan.plan_id).state is ActionPlanState.UNCERTAIN
    reconciliation = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]
    assert reconciliation["phase"] == "action_reconciling"

    completed = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "action-reconciled-1",
            "state": "completed",
            "phase": "action_reconciling",
            "evidence": {"code": "action_reconciled", "plan_id": plan.plan_id},
        },
        headers=headers,
    )

    assert completed.status_code == 200
    assert repo.assignment(int(task["task_id"])).phase is AssignmentPhase.COMPLETED
    assert repo.action_plan_by_id(plan.plan_id).state is ActionPlanState.CONFIRMED


def test_mobile_ineligible_profile_confirms_trace_and_completes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    pool = repo.import_pool(
        "targets.csv",
        "f" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 2_000_000_000.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    task = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]

    response = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "profile-1",
            "state": "completed",
            "phase": "identity_confirmed",
            "evidence": {
                "observed_username": "target_user",
                "access_state": "available",
                "following": 10,
                "followers": 20,
                "video_count": 2,
                "post_handles": ["post:0", "post:1"],
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    assignment = repo.assignment(int(task["task_id"]))
    plan = repo.action_plan(round_id, assignment.identity_key, "device-1")
    assert assignment.phase is AssignmentPhase.COMPLETED
    assert plan is not None
    assert plan.effective_outcome is OutcomeKind.TRACE
    assert plan.state is ActionPlanState.CONFIRMED
