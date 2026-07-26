from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.api import create_app


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
