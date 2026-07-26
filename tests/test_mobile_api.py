from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.acquisition_db import AcquisitionRepository
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
