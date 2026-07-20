import base64
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import DeviceDiagnostics
from tikpoc.acquisition_service import merge_browser_health_rows
from tikpoc.api import create_app
from tikpoc.db import Database
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


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


def _seeded_operations_app(
    tmp_path: Path,
    *,
    registry: WebAccountRegistry | None = None,
    clock_seconds: int = 12,
) -> tuple[object, str, int]:
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
    database = Database(path)
    database.migrate()
    database.upsert_browser_health(
        "account-01",
        "messages",
        device_id="phone-01",
        status="healthy",
        observed_at_ms=11_500,
        detail="ready",
    )
    return (
        create_app(path, clock=lambda: clock_seconds, registry=registry),
        round_id,
        deferred_id,
    )


def _legacy_control_database(tmp_path: Path) -> tuple[Path, str, dict[str, int]]:
    path = tmp_path / "legacy-controls.db"
    repository = AcquisitionRepository(path, clock_ms=lambda: 1_000)
    repository.migrate()
    pool = repository.import_pool("legacy.csv", "d" * 64, (_target(1),))
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={
            "phone-01": "seed-01",
            "phone-02": "seed-02",
            "phone-03": "seed-03",
        },
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    with sqlite3.connect(path) as connection:
        assignment_ids = {
            str(device_id): int(assignment_id)
            for assignment_id, device_id in connection.execute(
                "SELECT assignment_id, device_id FROM round_assignments"
            )
        }
        connection.execute("DROP TABLE operator_control_states")
        connection.execute(
            """
            CREATE TABLE operator_commands (
                command_id TEXT PRIMARY KEY,
                command_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            )
            """
        )

        def command_row(
            command_id: str,
            command: str,
            scope: str,
            scope_id: str,
            created_at_ms: int,
            *,
            result_json: str | None = None,
        ) -> tuple[str, str, str, str, int]:
            state = {"start": "running", "pause": "paused", "stop": "stopped"}[command]
            payload = {"scope": scope, "scope_id": scope_id}
            result = {
                "command_id": command_id,
                "command": command,
                **payload,
                "state": state,
            }
            return (
                command_id,
                command,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                result_json
                or json.dumps(result, sort_keys=True, separators=(",", ":")),
                created_at_ms,
            )

        connection.executemany(
            "INSERT INTO operator_commands VALUES (?, ?, ?, ?, ?)",
            (
                command_row("device-start", "start", "device", "phone-01", 100),
                command_row("device-pause", "pause", "device", "phone-01", 200),
                command_row(
                    "assignment-pause",
                    "pause",
                    "assignment",
                    str(assignment_ids["phone-02"]),
                    150,
                ),
                command_row(
                    "assignment-stop",
                    "stop",
                    "assignment",
                    str(assignment_ids["phone-02"]),
                    250,
                ),
                command_row(
                    "failed-device",
                    "pause",
                    "device",
                    "phone-03",
                    300,
                    result_json=json.dumps(
                        {
                            "failure": {
                                "kind": "conflict",
                                "message": "device state does not allow command",
                            }
                        }
                    ),
                ),
                command_row("fleet-pause", "pause", "fleet", "all", 350),
                command_row("round-pause", "pause", "round", round_id, 400),
                command_row("oversized-device", "pause", "device", "x" * 201, 450),
                (
                    "malformed-device",
                    "pause",
                    "{",
                    "{",
                    500,
                ),
            ),
        )
    return path, round_id, assignment_ids


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
        "uncertain",
        "remaining",
        "rolling_window_started_at_ms",
        "token_ready",
        "next_due_at_ms",
        "candidate_weight",
    }
    assert sum(row["reserved"] for row in payload["quotas"]) == 1
    assert sum(row["uncertain"] for row in payload["quotas"]) == 1
    assert payload["recent_mobile_traces"][0]["username"] == "buyer_1"
    assert payload["browser_health"][0]["account_id"] == "account-01"
    assert payload["devices"][-1]["latest_diagnostic"]["ui_summary"] == (
        "visible selector missing"
    )


def test_operations_snapshot_reads_browser_health_in_acquisition_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)

    def reject_secondary_snapshot(self) -> None:
        raise AssertionError("browser health used a second database connection")

    monkeypatch.setattr(
        Database,
        "browser_health_snapshot",
        reject_secondary_snapshot,
    )

    response = TestClient(app).get(f"/api/operations?round_id={round_id}")

    assert response.status_code == 200
    assert response.json()["browser_health"] == [
        {
            "account_id": "account-01",
            "page_role": "messages",
            "device_id": "phone-01",
            "browser_profile_label": "",
            "expected_tiktok_username": "",
            "observed_username": "",
            "binding_state": "stale",
            "status": "stale",
            "observed_at_ms": 11_500,
            "last_scan_at_ms": 0,
            "last_success_at_ms": 0,
            "scan_state": "not_started",
            "detail": "ready",
        }
    ]


def test_browser_health_requires_a_fresh_successful_scan() -> None:
    rows = merge_browser_health_rows(
        (),
        [
            {
                "account_id": "account-01",
                "page_role": "messages",
                "device_id": "phone-01",
                "status": "ready",
                "observed_at_ms": 299_000,
                "last_scan_at_ms": 299_000,
                "last_success_at_ms": 100_000,
                "scan_state": "error",
            }
        ],
        now_ms=300_000,
    )

    assert rows == [
        {
            "account_id": "account-01",
            "page_role": "messages",
            "device_id": "phone-01",
            "browser_profile_label": "",
            "expected_tiktok_username": "",
            "observed_username": "",
            "binding_state": "stale",
            "status": "stale",
            "observed_at_ms": 299_000,
            "last_scan_at_ms": 299_000,
            "last_success_at_ms": 100_000,
            "scan_state": "error",
            "detail": "",
        }
    ]


def test_operations_snapshot_expands_12_browser_accounts_and_local_states(
    tmp_path: Path,
) -> None:
    registry = WebAccountRegistry(
        tuple(
            WebAccount(
                account_id=f"account-{index:02d}",
                device_id=f"phone-{index:02d}",
                expected_tiktok_username=f"shop_{index}",
                browser_profile_label=f"客服 Profile {index}",
            )
            for index in range(1, 13)
        )
    )
    app, round_id, _ = _seeded_operations_app(
        tmp_path,
        registry=registry,
        clock_seconds=200,
    )
    database = app.state.database
    for account_id, role, state, observed, observed_at in (
        ("account-01", "messages", "ready", "shop_1", 199_500),
        ("account-02", "activity", "mismatch", "shop_other", 199_500),
        ("account-03", "messages", "signed_out", "", 199_500),
        ("account-04", "activity", "verification_required", "", 199_500),
        ("account-05", "messages", "ready", "shop_5", 199_500),
        ("account-06", "activity", "ready", "shop_6", 1_000),
    ):
        database.upsert_browser_health(
            account_id,
            role,
            device_id=registry.by_account_id(account_id).device_id,
            status=state,
            observed_at_ms=observed_at,
            observed_username=observed,
            last_scan_at_ms=observed_at if state == "ready" else 0,
            last_success_at_ms=observed_at if state == "ready" else 0,
            scan_state="idle" if state == "ready" else "not_started",
        )

    response = TestClient(app).get(f"/api/operations?round_id={round_id}")

    assert response.status_code == 200
    rows = response.json()["browser_health"]
    assert len(rows) == 24
    by_key = {(row["account_id"], row["page_role"]): row for row in rows}
    assert by_key[("account-01", "activity")]["binding_state"] == "unbound"
    assert by_key[("account-01", "messages")]["binding_state"] == "ready"
    assert by_key[("account-02", "activity")]["binding_state"] == "mismatch"
    assert by_key[("account-03", "messages")]["binding_state"] == "signed_out"
    assert (
        by_key[("account-04", "activity")]["binding_state"] == "verification_required"
    )
    assert by_key[("account-06", "activity")]["binding_state"] == "stale"
    assert by_key[("account-12", "messages")]["browser_profile_label"] == (
        "客服 Profile 12"
    )
    assert by_key[("account-12", "messages")]["expected_tiktok_username"] == ("shop_12")


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


def test_diagnostic_screenshot_serves_only_bounded_image_evidence(
    tmp_path: Path,
) -> None:
    app, _, assignment_id = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    outside_id = client.get(f"/api/diagnostics/{assignment_id}").json()["attempts"][0][
        "screenshot_id"
    ]
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    evidence = screenshots / "assignment.png"
    evidence.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )
    with sqlite3.connect(tmp_path / "tikpoc.db") as connection:
        connection.execute(
            "UPDATE action_attempts SET diagnostics_json=?",
            (
                json.dumps(
                    {
                        "screenshot_path": str(evidence),
                        "ui_summary": "visible selector missing",
                    }
                ),
            ),
        )

    screenshot_id = client.get(f"/api/diagnostics/{assignment_id}").json()["attempts"][
        0
    ]["screenshot_id"]
    response = client.get(f"/api/diagnostic-screenshots/{screenshot_id}")

    assert response.status_code == 200
    assert response.content == evidence.read_bytes()
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "assignment.png" not in str(response.headers)
    assert (
        client.get("/api/diagnostic-screenshots/000000000000000000000000").status_code
        == 404
    )
    assert client.get(f"/api/diagnostic-screenshots/{outside_id}").status_code == 404


def test_diagnostic_screenshot_rejects_non_image_and_oversize_files(
    tmp_path: Path,
) -> None:
    app, _, assignment_id = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    invalid = screenshots / "diagnostic.txt"
    invalid.write_text("not an image")

    def set_screenshot(path: Path) -> str:
        with sqlite3.connect(tmp_path / "tikpoc.db") as connection:
            connection.execute(
                "UPDATE action_attempts SET diagnostics_json=?",
                (json.dumps({"screenshot_path": str(path)}),),
            )
        return client.get(f"/api/diagnostics/{assignment_id}").json()["attempts"][0][
            "screenshot_id"
        ]

    invalid_id = set_screenshot(invalid)
    assert client.get(f"/api/diagnostic-screenshots/{invalid_id}").status_code == 404

    disguised = screenshots / "not-an-image.png"
    disguised.write_bytes(b"plain text with an image extension")
    disguised_id = set_screenshot(disguised)
    assert client.get(f"/api/diagnostic-screenshots/{disguised_id}").status_code == 404

    oversize = screenshots / "oversize.png"
    with oversize.open("wb") as output:
        output.write(b"\x89PNG\r\n\x1a\n")
        output.truncate(10 * 1024 * 1024 + 1)
    oversize_id = set_screenshot(oversize)
    assert client.get(f"/api/diagnostic-screenshots/{oversize_id}").status_code == 404


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


def test_migration_backfills_latest_successful_legacy_scoped_controls(
    tmp_path: Path,
) -> None:
    path, round_id, assignment_ids = _legacy_control_database(tmp_path)

    app = create_app(path, clock=lambda: 2)
    repository = app.state.acquisition

    with sqlite3.connect(path) as connection:
        controls = connection.execute(
            """
            SELECT scope, scope_id, state, command_id
            FROM operator_control_states ORDER BY scope, scope_id
            """
        ).fetchall()
    assert controls == [
        (
            "assignment",
            str(assignment_ids["phone-02"]),
            "stopped",
            "assignment-stop",
        ),
        ("device", "phone-01", "paused", "device-pause"),
    ]
    assert (
        repository.claim_next_assignment(
            round_id, "phone-01", "worker-device", now_ms=2_000
        )
        is None
    )
    assert (
        repository.claim_next_assignment(
            round_id, "phone-02", "worker-assignment", now_ms=2_000
        )
        is None
    )
    assert (
        repository.claim_next_assignment(
            round_id, "phone-03", "worker-unblocked", now_ms=2_000
        )
        is not None
    )


def test_fleet_start_and_pause_skip_terminal_rounds(tmp_path: Path) -> None:
    app, stopped_round_id, _ = _seeded_operations_app(tmp_path)
    repository = app.state.acquisition
    live_pool = repository.import_pool("live.csv", "e" * 64, (_target(3),))
    live_round_id = create_exposure_round(
        repository,
        pool_id=live_pool.pool_id,
        device_seeds={"phone-01": "live-seed"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    completed_pool = repository.import_pool("completed.csv", "f" * 64, (_target(4),))
    completed_round_id = create_exposure_round(
        repository,
        pool_id=completed_pool.pool_id,
        device_seeds={"phone-01": "completed-seed"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE exposure_rounds SET state='stopped' WHERE round_id=?",
            (stopped_round_id,),
        )
        connection.execute(
            "UPDATE exposure_rounds SET state='completed' WHERE round_id=?",
            (completed_round_id,),
        )
    client = TestClient(app)
    start_body = {"command_id": "start-eligible", "scope": "fleet", "scope_id": "all"}

    started = client.post("/api/commands/start", json=start_body)
    replayed = client.post("/api/commands/start", json=start_body)
    paused = client.post(
        "/api/commands/pause",
        json={"command_id": "pause-eligible", "scope": "fleet", "scope_id": "all"},
    )

    assert started.status_code == 200
    assert replayed.status_code == 200
    assert replayed.json() == started.json()
    assert paused.status_code == 200
    with sqlite3.connect(repository.path) as connection:
        states = {
            row[0]: row[1]
            for row in connection.execute("SELECT round_id, state FROM exposure_rounds")
        }
    assert states == {
        stopped_round_id: "stopped",
        live_round_id: "paused",
        completed_round_id: "completed",
    }


def test_controls_reject_missing_device_and_do_not_revive_stopped_rounds(
    tmp_path: Path,
) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    missing_device = client.post(
        "/api/commands/pause",
        json={
            "command_id": "device-1",
            "scope": "device",
            "scope_id": "missing-phone",
        },
    )
    stopped = client.post(
        "/api/commands/stop",
        json={"command_id": "stop-all", "scope": "fleet", "scope_id": "all"},
    )
    restarted = client.post(
        "/api/commands/start",
        json={"command_id": "restart-all", "scope": "fleet", "scope_id": "all"},
    )

    assert missing_device.status_code == 404
    assert stopped.status_code == 200
    assert restarted.status_code == 200
    assert restarted.json()["state"] == "running"
    rounds = client.get("/api/rounds").json()["items"]
    assert next(item for item in rounds if item["round_id"] == round_id)["state"] == (
        "stopped"
    )


def test_device_controls_pause_resume_and_stop_future_claims(tmp_path: Path) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    repository = app.state.acquisition

    paused = client.post(
        "/api/commands/pause",
        json={
            "command_id": "pause-phone",
            "scope": "device",
            "scope_id": "phone-02",
        },
    )

    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"
    assert (
        repository.claim_next_assignment(
            round_id, "phone-02", "worker-02", now_ms=12_000
        )
        is None
    )
    devices = client.get(f"/api/operations?round_id={round_id}").json()["devices"]
    assert (
        next(row for row in devices if row["device_id"] == "phone-02")["control_state"]
        == "paused"
    )

    resumed = client.post(
        "/api/commands/start",
        json={
            "command_id": "resume-phone",
            "scope": "device",
            "scope_id": "phone-02",
        },
    )
    assert resumed.status_code == 200
    claimed = repository.claim_next_assignment(
        round_id, "phone-02", "worker-02", now_ms=12_001
    )
    assert claimed is not None

    stopped = client.post(
        "/api/commands/stop",
        json={
            "command_id": "stop-phone",
            "scope": "device",
            "scope_id": "phone-03",
        },
    )
    restarted = client.post(
        "/api/commands/start",
        json={
            "command_id": "restart-phone",
            "scope": "device",
            "scope_id": "phone-03",
        },
    )
    assert stopped.status_code == 200
    assert restarted.status_code == 409
    assert (
        repository.claim_next_assignment(
            round_id, "phone-03", "worker-03", now_ms=12_001
        )
        is None
    )


def test_assignment_controls_are_durable_and_protect_active_leases(
    tmp_path: Path,
) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    repository = app.state.acquisition
    with sqlite3.connect(repository.path) as connection:
        row = connection.execute(
            """
            SELECT assignment_id FROM round_assignments
            WHERE round_id=? AND device_id='phone-02' AND phase='pending'
            ORDER BY assignment_id LIMIT 1
            """,
            (round_id,),
        ).fetchone()
        assert row is not None
        assignment_id = int(row[0])
        connection.execute(
            """
            UPDATE round_assignments SET phase='completed', completed_at_ms=11_000
            WHERE round_id=? AND device_id='phone-02' AND assignment_id<>?
            """,
            (round_id, assignment_id),
        )

    paused = client.post(
        "/api/commands/pause",
        json={
            "command_id": "pause-assignment",
            "scope": "assignment",
            "scope_id": str(assignment_id),
        },
    )
    assert paused.status_code == 200
    assert (
        repository.claim_next_assignment(
            round_id, "phone-02", "worker-assignment", now_ms=12_000
        )
        is None
    )
    coverage = client.get(f"/api/coverage?round_id={round_id}").json()["items"]
    assignment_row = next(
        device
        for target in coverage
        for device in target["devices"]
        if device["assignment_id"] == assignment_id
    )
    assert assignment_row["control_state"] == "paused"

    resumed = client.post(
        "/api/commands/start",
        json={
            "command_id": "resume-assignment",
            "scope": "assignment",
            "scope_id": str(assignment_id),
        },
    )
    assert resumed.status_code == 200
    claimed = repository.claim_next_assignment(
        round_id, "phone-02", "worker-assignment", now_ms=12_001
    )
    assert claimed is not None and claimed.assignment_id == assignment_id

    active_pause = client.post(
        "/api/commands/pause",
        json={
            "command_id": "pause-active-assignment",
            "scope": "assignment",
            "scope_id": str(assignment_id),
        },
    )
    assert active_pause.status_code == 409

    repository.defer_assignment(
        assignment_id,
        "worker-assignment",
        now_ms=12_002,
        retry_delay_ms=0,
        error_code="operator_test",
        diagnostics=DeviceDiagnostics(),
    )
    stopped = client.post(
        "/api/commands/stop",
        json={
            "command_id": "stop-assignment",
            "scope": "assignment",
            "scope_id": str(assignment_id),
        },
    )
    restarted = client.post(
        "/api/commands/start",
        json={
            "command_id": "restart-assignment",
            "scope": "assignment",
            "scope_id": str(assignment_id),
        },
    )
    assert stopped.status_code == 200
    assert restarted.status_code == 409
    assert (
        repository.claim_next_assignment(
            round_id, "phone-02", "worker-after-stop", now_ms=12_003
        )
        is None
    )


def test_skipped_assignment_rejects_operator_control_commands(tmp_path: Path) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    repository = app.state.acquisition
    with sqlite3.connect(repository.path) as connection:
        assignment_id = int(
            connection.execute(
                """
                SELECT assignment_id FROM round_assignments
                WHERE round_id = ? ORDER BY assignment_id LIMIT 1
                """,
                (round_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'skipped', completed_at_ms = 11_000,
                last_error_code = 'profile_unreachable'
            WHERE assignment_id = ?
            """,
            (assignment_id,),
        )

    response = client.post(
        "/api/commands/pause",
        json={
            "command_id": "pause-skipped-assignment",
            "scope": "assignment",
            "scope_id": str(assignment_id),
        },
    )

    assert response.status_code == 409
    assert "terminal assignment" in response.json()["error"]


def test_failed_commands_are_persisted_and_bound_to_original_content(
    tmp_path: Path,
) -> None:
    app, round_id, _ = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    missing = {
        "command_id": "missing-round-command",
        "scope": "round",
        "scope_id": "missing-round",
    }

    first = client.post("/api/commands/start", json=missing)
    replay = client.post("/api/commands/start", json=missing)
    conflicting = client.post(
        "/api/commands/start", json={**missing, "scope_id": round_id}
    )

    assert first.status_code == replay.status_code == 404
    assert replay.json() == first.json() == {"error": "command target not found"}
    assert conflicting.status_code == 409
    assert conflicting.json() == {"error": "command id has different content"}
    assert client.get("/api/rounds").json()["items"][0]["state"] == "pending"
    with sqlite3.connect(app.state.acquisition.path) as connection:
        row = connection.execute(
            """
            SELECT command_type, payload_json, result_json
            FROM operator_commands WHERE command_id='missing-round-command'
            """
        ).fetchone()
    assert row is not None
    assert row[0] == "start"
    assert json.loads(row[1]) == {"scope": "round", "scope_id": "missing-round"}
    assert json.loads(row[2])["failure"] == {
        "kind": "not_found",
        "message": "command target not found",
    }


def test_conflicting_retry_failure_is_replayed_after_assignment_changes(
    tmp_path: Path,
) -> None:
    app, _, deferred_id = _seeded_operations_app(tmp_path)
    client = TestClient(app)
    with sqlite3.connect(app.state.acquisition.path) as connection:
        pending_id = int(
            connection.execute(
                "SELECT assignment_id FROM round_assignments WHERE phase='pending' LIMIT 1"
            ).fetchone()[0]
        )
    body = {"command_id": "failed-retry", "assignment_id": pending_id}

    first = client.post("/api/commands/retry", json=body)
    with sqlite3.connect(app.state.acquisition.path) as connection:
        connection.execute(
            """
            UPDATE round_assignments SET phase='deferred', next_attempt_at_ms=13000
            WHERE assignment_id=?
            """,
            (pending_id,),
        )
    replay = client.post("/api/commands/retry", json=body)
    conflicting = client.post(
        "/api/commands/retry",
        json={"command_id": "failed-retry", "assignment_id": deferred_id},
    )

    assert first.status_code == replay.status_code == 409
    assert replay.json() == first.json() == {"error": "assignment is not retryable"}
    assert conflicting.status_code == 409
    with sqlite3.connect(app.state.acquisition.path) as connection:
        assert (
            connection.execute(
                "SELECT next_attempt_at_ms FROM round_assignments WHERE assignment_id=?",
                (pending_id,),
            ).fetchone()[0]
            == 13_000
        )
