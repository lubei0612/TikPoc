import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.api import create_app
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def _target(index: int) -> Target:
    return Target(
        target_id=f"user-{index}",
        username=f"buyer_{index}",
        profile_url=f"https://www.tiktok.com/@buyer_{index}",
        source_video_id=f"video-{index}",
        sec_uid=f"sec-{index}",
        identity_key=f"sec:sec-{index}",
        source_line_numbers=(index + 2,),
    )


def _seeded_operations_app(tmp_path: Path) -> tuple[object, str, int]:
    path = tmp_path / "tikpoc.db"
    repository = AcquisitionRepository(path, clock_ms=lambda: 10_000)
    repository.migrate()
    pool = repository.import_pool("comments.csv", "a" * 64, (_target(1), _target(2)))
    devices = {
        "phone-01": "seed-01",
        "phone-02": "seed-02",
        "phone-03": "seed-03",
    }
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds=devices,
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    for index, device_id in enumerate(devices, start=1):
        repository.record_fleet_device_health(
            device_id,
            f"account-{index:02d}",
            "healthy",
            now_ms=9_000,
            fence_token=index,
        )
    with sqlite3.connect(path) as connection:
        assignment_rows = connection.execute(
            """
            SELECT assignment_id, identity_key, device_id
            FROM round_assignments ORDER BY assignment_id
            """
        ).fetchall()
        first_id = int(assignment_rows[0][0])
        deferred_id = int(assignment_rows[-1][0])
        connection.execute(
            """
            UPDATE round_assignments
            SET phase='completed', visit_confirmed_at_ms=2000, completed_at_ms=2500
            WHERE assignment_id = ?
            """,
            (first_id,),
        )
        connection.execute(
            """
            UPDATE round_assignments
            SET phase='deferred', attempt_count=2, next_attempt_at_ms=12000,
                last_error_code='selector_missing'
            WHERE assignment_id = ?
            """,
            (deferred_id,),
        )
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'pending', 'profile_opening', '{}', 1500)
            """,
            (first_id,),
        )
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, 'profile_opening', 'completed', '{}', 2500)
            """,
            (first_id,),
        )
        connection.execute(
            """
            INSERT INTO acquisition_quota_windows(
                device_id, outcome, window_start_ms,
                reserved_count, confirmed_count, uncertain_count
            ) VALUES ('phone-01', 'like', 0, 3, 2, 0)
            """
        )
        connection.execute(
            """
            INSERT INTO device_action_plans(
                round_id, identity_key, device_id, seed, requested_outcome,
                effective_outcome, state, created_at_ms
            ) VALUES (?, ?, ?, 'plan-seed', 'like', 'like', 'uncertain', 1000)
            """,
            (round_id, assignment_rows[-1][1], assignment_rows[-1][2]),
        )
        plan_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO action_attempts(
                plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
            ) VALUES (?, 1, 'uncertain', ?, 11000)
            """,
            (
                plan_id,
                '{"screenshot_path":"/private/customer/screen.png",'
                '"ui_summary":"visible selector missing"}',
            ),
        )
    return create_app(path, clock=lambda: 12), round_id, deferred_id


def test_operations_snapshot_contains_dynamic_round_devices_and_traces(
    tmp_path: Path,
) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)

    response = TestClient(app).get(f"/api/operations?round_id={round_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["round"]["round_id"] == round_id
    assert payload["round"]["target_count"] == 2
    assert payload["coverage"]["required_devices"] == 3
    assert len(payload["devices"]) == 3
    assert set(payload["devices"][0]) >= {
        "device_id",
        "health",
        "current_assignment",
        "mean_ms",
        "p90_ms",
    }
    assert payload["devices"][0]["mean_ms"] == 1_000
    assert payload["devices"][0]["p90_ms"] == 1_000
    assert set(payload["quotas"][0]) >= {
        "device_id",
        "outcome",
        "limit",
        "reserved",
        "confirmed",
        "remaining",
        "resets_at_ms",
    }
    assert payload["recent_mobile_traces"][0]["username"] == "buyer_1"


def test_pool_round_lists_and_paginated_coverage_are_bounded(tmp_path: Path) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)

    pools = client.get("/api/pools?limit=10").json()
    rounds = client.get("/api/rounds?limit=10").json()
    coverage = client.get(f"/api/coverage?round_id={round_id}&offset=1&limit=1").json()

    assert pools["items"][0]["unique_targets"] == 2
    assert rounds["items"][0]["device_count"] == 3
    assert coverage["total"] == 2
    assert coverage["offset"] == 1
    assert coverage["limit"] == 1
    assert len(coverage["items"]) == 1
    assert len(coverage["items"][0]["devices"]) == 3
    assert client.get(f"/api/coverage?round_id={round_id}&limit=501").status_code == 422


def test_round_commands_are_persisted_and_idempotent(tmp_path: Path) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)

    start_body = {
        "command_id": "start-1",
        "scope": "round",
        "scope_id": round_id,
    }
    started = client.post("/api/commands/start", json=start_body)
    pause_body = {
        "command_id": "pause-1",
        "scope": "round",
        "scope_id": round_id,
    }
    first = client.post("/api/commands/pause", json=pause_body)
    second = client.post("/api/commands/pause", json=pause_body)
    conflict = client.post(
        "/api/commands/stop", json={**pause_body, "scope_id": "different-round"}
    )

    assert started.status_code == 200
    assert started.json()["state"] == "running"
    assert first.status_code == 200
    assert first.json()["state"] == "paused"
    assert second.json() == first.json()
    assert conflict.status_code == 409
    assert client.get("/api/rounds").json()["items"][0]["state"] == "paused"


def test_stop_and_retry_commands_apply_once(tmp_path: Path) -> None:
    app, round_id, assignment_id = _seeded_operations_app(tmp_path)
    client = TestClient(app)

    retried = client.post(
        "/api/commands/retry",
        json={"command_id": "retry-1", "assignment_id": assignment_id},
    )
    repeated = client.post(
        "/api/commands/retry",
        json={"command_id": "retry-1", "assignment_id": assignment_id},
    )
    stopped = client.post(
        "/api/commands/stop",
        json={
            "command_id": "stop-1",
            "scope": "round",
            "scope_id": round_id,
        },
    )

    assert retried.status_code == 200
    assert repeated.json() == retried.json()
    assert retried.json() == {
        "command_id": "retry-1",
        "assignment_id": assignment_id,
        "phase": "deferred",
        "retry_ready": True,
    }
    assert stopped.json()["state"] == "stopped"


def test_assignment_diagnostics_are_bounded_and_paths_are_opaque(
    tmp_path: Path,
) -> None:
    app, _, assignment_id = _seeded_operations_app(tmp_path)

    response = TestClient(app).get(f"/api/diagnostics/{assignment_id}?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["assignment_id"] == assignment_id
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["ui_summary"] == "visible selector missing"
    assert payload["attempts"][0]["screenshot_id"]
    assert "/private/" not in str(payload)
    assert (
        TestClient(app).get(f"/api/diagnostics/{assignment_id}?limit=101").status_code
        == 422
    )


def test_operator_command_models_reject_unknown_scope_and_extra_fields(
    tmp_path: Path,
) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)

    unknown_scope = client.post(
        "/api/commands/start",
        json={"command_id": "bad-1", "scope": "seven", "scope_id": round_id},
    )
    oversized = client.post(
        "/api/commands/start",
        json={"command_id": "x" * 101, "scope": "round", "scope_id": round_id},
    )

    assert unknown_scope.status_code == 422
    assert oversized.status_code == 422
