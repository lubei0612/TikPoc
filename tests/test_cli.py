import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
import uvicorn

from tikpoc import cli, runner, web_worker
from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.cli import main
from tikpoc.db import Database

from tests.test_importer import HEADER


def _write_acquisition_csv(path: Path, *, count: int = 2) -> None:
    rows = [
        (
            f"{index},comment_panel,744,https://video,user-{index},sec-{index},"
            f"buyer_{index},Name,https://profile/{index},,hello,0,0,false,now\n"
        )
        for index in range(1, count + 1)
    ]
    path.write_text(HEADER + "".join(rows), encoding="utf-8")


def _write_fleet_config(path: Path) -> None:
    path.write_text(
        """
myt:
  host: 192.0.2.10
  sdk_port: 8000
proxy_relay:
  bind_host: 192.0.2.20
  bind_port: 7898
  upstream_host: 127.0.0.1
  upstream_port: 7897
devices:
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.0.2.10:30000
    appium_url: http://127.0.0.1:4723
    order_seed: seed-a
  - device_id: phone-02
    account_id: account-02
    myt_slot: 2
    adb_endpoint: 192.0.2.10:30100
    appium_url: http://127.0.0.1:4723
    order_seed: seed-b
""".lstrip(),
        encoding="utf-8",
    )


def _empty_database(path: Path) -> None:
    sqlite3.connect(path).close()


def _schema_objects(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_schema
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )


def _write_browser_accounts(path: Path) -> None:
    path.write_text(
        """
accounts:
  - account_id: account-01
    device_id: phone-01
    mode: browser
    expected_tiktok_username: shop_one
    browser_profile_label: TikPoc 01
    enabled: true
  - account_id: account-02
    device_id: phone-02
    mode: browser
    expected_tiktok_username: shop_two
    browser_profile_label: TikPoc 02
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )


def _browser_bindings_payload() -> dict[str, object]:
    return {
        "accounts": [
            {
                "account_id": f"account-0{index}",
                "device_id": f"phone-0{index}",
                "expected_tiktok_username": f"shop_{'one' if index == 1 else 'two'}",
                "browser_profile_label": f"TikPoc 0{index}",
                "enabled": True,
                "binding_ready": True,
            }
            for index in (1, 2)
        ]
    }


def _browser_health_payload(*, ready: bool = True) -> dict[str, object]:
    rows = []
    for index in (1, 2):
        username = f"shop_{'one' if index == 1 else 'two'}"
        for role in ("activity", "messages"):
            rows.append(
                {
                    "account_id": f"account-0{index}",
                    "page_role": role,
                    "browser_profile_label": f"TikPoc 0{index}",
                    "expected_tiktok_username": username,
                    "observed_username": username if ready else "",
                    "binding_state": "ready" if ready else "unbound",
                    "observed_at_ms": 1_720_000_000_000 if ready else 0,
                    "message_text": "must never print",
                    "private_destination": "must never print",
                }
            )
    return {"browser_health": rows}


def test_cli_browser_status_prints_only_redacted_health(monkeypatch, capsys) -> None:
    from tikpoc import browser_connect

    monkeypatch.setattr(
        browser_connect,
        "fetch_json",
        lambda _url: _browser_health_payload(),
    )

    assert main(["browser", "status", "--dashboard-url", "http://127.0.0.1:8766"]) == 0
    output = capsys.readouterr().out
    assert "account-01" in output
    assert "activity" in output
    assert "must never print" not in output


def test_cli_browser_connect_waits_for_every_account_role(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tikpoc import browser_connect

    config_path = tmp_path / "web-accounts.yaml"
    _write_browser_accounts(config_path)

    def fake_fetch(url: str) -> dict[str, object]:
        return (
            _browser_bindings_payload()
            if url.endswith("/api/browser-bindings")
            else _browser_health_payload()
        )

    monkeypatch.setattr(browser_connect, "fetch_json", fake_fetch)

    result = main(
        [
            "browser",
            "connect",
            "--web-accounts",
            str(config_path),
            "--dashboard-url",
            "http://127.0.0.1:8766",
            "--timeout",
            "0",
        ]
    )

    assert result == 0
    assert "ready=4/4" in capsys.readouterr().out


def test_cli_browser_connect_returns_nonzero_on_timeout(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tikpoc import browser_connect

    config_path = tmp_path / "web-accounts.yaml"
    _write_browser_accounts(config_path)

    def fake_fetch(url: str) -> dict[str, object]:
        return (
            _browser_bindings_payload()
            if url.endswith("/api/browser-bindings")
            else _browser_health_payload(ready=False)
        )

    monkeypatch.setattr(browser_connect, "fetch_json", fake_fetch)

    result = main(
        [
            "browser",
            "connect",
            "--web-accounts",
            str(config_path),
            "--dashboard-url",
            "http://127.0.0.1:8766",
            "--timeout",
            "0",
        ]
    )

    assert result == 1
    assert "ready=0/4" in capsys.readouterr().out


def test_cli_browser_connect_rejects_registry_server_mismatch(
    tmp_path: Path, monkeypatch
) -> None:
    from tikpoc import browser_connect

    config_path = tmp_path / "web-accounts.yaml"
    _write_browser_accounts(config_path)
    monkeypatch.setattr(
        browser_connect,
        "fetch_json",
        lambda _url: {"accounts": [_browser_bindings_payload()["accounts"][0]]},
    )

    with pytest.raises(SystemExit, match="do not match the account registry"):
        main(
            [
                "browser",
                "connect",
                "--web-accounts",
                str(config_path),
                "--timeout",
                "0",
            ]
        )


def test_cli_browser_guide_prints_the_extension_directory(capsys) -> None:
    extension = Path("/tmp/tikpoc-extension")

    assert main(["browser", "guide", "--extension-path", str(extension)]) == 0
    output = capsys.readouterr().out
    assert str(extension) in output
    assert "Command+Shift+G" in output


def test_cli_pool_import_creates_an_idempotent_acquisition_pool(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "comments.csv"
    database_path = tmp_path / "acquisition.db"
    _write_acquisition_csv(source)

    first = main(["pool-import", "--db", str(database_path), "--csv", str(source)])
    first_output = capsys.readouterr().out
    second = main(["pool-import", "--db", str(database_path), "--csv", str(source)])
    second_output = capsys.readouterr().out

    assert first == second == 0
    assert "unique_targets=2" in first_output
    assert second_output == first_output
    pool_id = first_output.split()[0].split("=", 1)[1]
    assert len(AcquisitionRepository(database_path).pool_targets(pool_id)) == 2


def test_cli_pool_import_validates_source_before_database_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "acquisition.db"

    with pytest.raises(SystemExit, match="CSV file does not exist"):
        main(
            [
                "pool-import",
                "--db",
                str(database_path),
                "--csv",
                str(tmp_path / "missing.csv"),
            ]
        )

    assert not database_path.exists()


def test_cli_proxy_guard_runs_one_redacted_cycle(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "devices.yaml"
    _write_fleet_config(config_path)

    def fake_run(config, adb_path):
        assert len(config.devices) == 2
        assert adb_path == Path("/sdk/adb")
        return (
            type(
                "Health",
                (),
                {
                    "device_id": "phone-01",
                    "adb_state": "device",
                    "proxy_state": "healthy",
                    "http_status": 200,
                    "http_state": "ok",
                    "observed_at_ms": 123456789,
                },
            )(),
            type(
                "Health",
                (),
                {
                    "device_id": "phone-02",
                    "adb_state": "device",
                    "proxy_state": "corrected",
                    "http_status": 200,
                    "http_state": "ok",
                    "observed_at_ms": 123456789,
                },
            )(),
        )

    monkeypatch.setattr(cli, "_run_proxy_guard", fake_run, raising=False)

    result = main(
        [
            "proxy-guard",
            "--devices",
            str(config_path),
            "--adb-path",
            "/sdk/adb",
            "--once",
        ]
    )

    assert result == 0
    assert capsys.readouterr().out == (
        "observed_at_ms=123456789 device_id=phone-01 adb_state=device "
        "proxy_state=healthy http_state=ok http_status=200\n"
        "observed_at_ms=123456789 device_id=phone-02 adb_state=device "
        "proxy_state=corrected http_state=ok http_status=200\n"
        "devices=2 healthy=1 corrected=1 failed=0 http_200=2 http_unknown=0\n"
    )


def test_cli_proxy_guard_counts_http_failures_as_failed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "devices.yaml"
    _write_fleet_config(config_path)
    row = type(
        "Health",
        (),
        {
            "device_id": "phone-01",
            "adb_state": "device",
            "proxy_state": "healthy",
            "http_status": 403,
            "http_state": "failed",
            "observed_at_ms": 123456789,
        },
    )()
    monkeypatch.setattr(cli, "_run_proxy_guard", lambda *_args: (row,))

    assert main(["proxy-guard", "--devices", str(config_path), "--once"]) == 0

    assert capsys.readouterr().out.endswith(
        "devices=1 healthy=0 corrected=0 failed=1 http_200=0 http_unknown=0\n"
    )


def test_cli_proxy_guard_counts_device_vpn_states_as_success(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "devices.yaml"
    _write_fleet_config(config_path)

    def row(device_id: str, proxy_state: str):
        return type(
            "Health",
            (),
            {
                "device_id": device_id,
                "adb_state": "device",
                "proxy_state": proxy_state,
                "http_status": None,
                "http_state": "unknown",
                "observed_at_ms": 123456789,
            },
        )()

    monkeypatch.setattr(
        cli,
        "_run_proxy_guard",
        lambda *_args: (
            row("phone-01", "vpn_healthy"),
            row("phone-02", "vpn_recovered"),
        ),
    )

    assert main(["proxy-guard", "--devices", str(config_path), "--once"]) == 0

    assert capsys.readouterr().out.endswith(
        "devices=2 healthy=1 corrected=1 failed=0 http_200=0 http_unknown=2\n"
    )


def test_cli_proxy_guard_validates_inputs_before_running(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(SystemExit, match="device configuration does not exist"):
        main(["proxy-guard", "--devices", str(missing), "--once"])

    config_path = tmp_path / "devices.yaml"
    _write_fleet_config(config_path)
    with pytest.raises(SystemExit, match="interval must be at least 5 seconds"):
        main(
            [
                "proxy-guard",
                "--devices",
                str(config_path),
                "--interval",
                "1",
            ]
        )


def test_cli_supabase_pool_import_uses_deterministic_pool_and_source_counts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "comments.csv"
    env_file = tmp_path / "supabase.env"
    _write_acquisition_csv(source)
    env_file.write_text(
        "SUPABASE_URL=https://project.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=service-secret\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    captured = {}

    class FakeStore:
        def import_pool(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "tikpoc.supabase_store.SupabaseBusinessStore.from_env_file",
        lambda path: FakeStore(),
    )

    result = main(
        [
            "supabase-pool-import",
            "--csv",
            str(source),
            "--env-file",
            str(env_file),
        ]
    )

    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    assert result == 0
    assert captured["pool_id"] == f"pool-{checksum[:20]}"
    assert captured["source_name"] == "comments.csv"
    assert captured["source_checksum"] == checksum
    assert captured["source_rows"] == 2
    assert len(captured["targets"]) == 2
    assert capsys.readouterr().out == (
        f"pool_id=pool-{checksum[:20]} unique_targets=2 source_rows=2 "
        "duplicates=0 invalid=0\n"
    )


def test_cli_round_create_materializes_configured_devices(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "comments.csv"
    database_path = tmp_path / "acquisition.db"
    config_path = tmp_path / "devices.yaml"
    _write_acquisition_csv(source)
    _write_fleet_config(config_path)
    main(["pool-import", "--db", str(database_path), "--csv", str(source)])
    pool_id = capsys.readouterr().out.split()[0].split("=", 1)[1]

    result = main(
        [
            "round-create",
            "--db",
            str(database_path),
            "--pool",
            pool_id,
            "--devices",
            str(config_path),
            "--starts-at",
            "2026-07-18T00:00:00+08:00",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    round_id = output.split()[0].split("=", 1)[1]
    assert AcquisitionRepository(database_path).assignment_count(round_id) == 4


def test_cli_round_create_rejects_missing_pool_before_schema_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "acquisition.db"
    config_path = tmp_path / "devices.yaml"
    _empty_database(database_path)
    _write_fleet_config(config_path)

    with pytest.raises(SystemExit, match="target pool does not exist: missing"):
        main(
            [
                "round-create",
                "--db",
                str(database_path),
                "--pool",
                "missing",
                "--devices",
                str(config_path),
                "--starts-at",
                "2026-07-18T00:00:00+08:00",
            ]
        )

    assert _schema_objects(database_path) == ()


def test_cli_round_create_requires_timezone_before_mutation(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "comments.csv"
    database_path = tmp_path / "acquisition.db"
    config_path = tmp_path / "devices.yaml"
    _write_acquisition_csv(source)
    _write_fleet_config(config_path)
    main(["pool-import", "--db", str(database_path), "--csv", str(source)])
    pool_id = capsys.readouterr().out.split()[0].split("=", 1)[1]

    with pytest.raises(SystemExit, match="starts-at must include a timezone"):
        main(
            [
                "round-create",
                "--db",
                str(database_path),
                "--pool",
                pool_id,
                "--devices",
                str(config_path),
                "--starts-at",
                "2026-07-18T00:00:00",
            ]
        )

    assert AcquisitionRepository(database_path).assignment_count("missing") == 0


def test_cli_assignment_retry_makes_only_deferred_work_immediately_eligible(
    tmp_path: Path, capsys
) -> None:
    from tests.test_capacity import repository_with_round

    repository, round_id = repository_with_round(tmp_path, target_count=1)
    with sqlite3.connect(repository.path) as connection:
        assignment_id = int(
            connection.execute(
                "SELECT MIN(assignment_id) FROM round_assignments WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE round_assignments
            SET phase = 'deferred', next_attempt_at_ms = 9999999999999
            WHERE assignment_id = ?
            """,
            (assignment_id,),
        )

    result = main(
        [
            "assignment-retry",
            "--db",
            str(repository.path),
            "--assignment",
            str(assignment_id),
        ]
    )

    assert result == 0
    assert repository.assignment(assignment_id).next_attempt_at_ms == 0
    assert f"assignment_id={assignment_id}" in capsys.readouterr().out


def test_cli_assignment_retry_rejects_missing_id_before_schema_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "acquisition.db"
    _empty_database(database_path)

    with pytest.raises(SystemExit, match="assignment does not exist: 999"):
        main(
            [
                "assignment-retry",
                "--db",
                str(database_path),
                "--assignment",
                "999",
            ]
        )

    assert _schema_objects(database_path) == ()


def test_cli_assignment_retry_rejects_non_deferred_work(tmp_path: Path) -> None:
    from tests.test_capacity import repository_with_round

    repository, round_id = repository_with_round(tmp_path, target_count=1)
    with sqlite3.connect(repository.path) as connection:
        assignment_id = int(
            connection.execute(
                "SELECT MIN(assignment_id) FROM round_assignments WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        )

    with pytest.raises(SystemExit, match="assignment is not deferred"):
        main(
            [
                "assignment-retry",
                "--db",
                str(repository.path),
                "--assignment",
                str(assignment_id),
            ]
        )

    assert repository.assignment(assignment_id).next_attempt_at_ms == 0


def test_cli_capacity_emits_stable_json_and_pass_exit_code(
    tmp_path: Path, capsys
) -> None:
    from tests.test_capacity import repository_with_round, seed_completed_round

    repository, round_id = repository_with_round(tmp_path)
    seed_completed_round(repository, round_id)

    result = main(
        [
            "capacity",
            "--db",
            str(repository.path),
            "--round",
            round_id,
            "--expected-devices",
            "2",
            "--target-count",
            "2",
            "--effective-hours",
            "20",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert list(payload) == [
        "devices",
        "fully_covered_targets",
        "measured_seconds",
        "passed",
        "projected_unique_per_day",
        "reasons",
        "slowest_device_id",
        "uncertain_count",
    ]
    assert payload["passed"] is True
    assert payload["slowest_device_id"] == "phone-02"


def test_cli_capacity_rejects_missing_round_before_schema_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "acquisition.db"
    _empty_database(database_path)

    with pytest.raises(SystemExit, match="round does not exist: missing"):
        main(
            [
                "capacity",
                "--db",
                str(database_path),
                "--round",
                "missing",
            ]
        )

    assert _schema_objects(database_path) == ()


def test_cli_capacity_text_separates_measured_and_projected_values(
    tmp_path: Path, capsys
) -> None:
    from tests.test_capacity import repository_with_round

    repository, round_id = repository_with_round(tmp_path, target_count=1)

    result = main(
        [
            "capacity",
            "--db",
            str(repository.path),
            "--round",
            round_id,
            "--expected-devices",
            "2",
            "--target-count",
            "1",
            "--effective-hours",
            "20",
        ]
    )
    output = capsys.readouterr().out

    assert result == 1
    assert "measured_seconds=" in output
    assert "projected_unique_per_day=" in output
    assert "passed=false" in output


def test_cli_fleet_run_validates_mapping_then_delegates(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.test_capacity import repository_with_round

    repository, round_id = repository_with_round(tmp_path)
    config_path = tmp_path / "devices.yaml"
    _write_fleet_config(config_path)
    captured = {}

    def fake_run(repository_arg, round_id_arg, config_arg) -> int:
        captured["repository"] = repository_arg
        captured["round_id"] = round_id_arg
        captured["config"] = config_arg
        return 17

    monkeypatch.setattr(cli, "_run_acquisition_fleet", fake_run, raising=False)

    result = main(
        [
            "fleet-run",
            "--db",
            str(repository.path),
            "--round",
            round_id,
            "--devices",
            str(config_path),
        ]
    )

    assert result == 17
    assert captured["repository"].path == repository.path
    assert captured["round_id"] == round_id
    assert [device.device_id for device in captured["config"].devices] == [
        "phone-01",
        "phone-02",
    ]


def test_cli_fleet_run_rejects_missing_round_before_schema_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "acquisition.db"
    config_path = tmp_path / "devices.yaml"
    _empty_database(database_path)
    _write_fleet_config(config_path)

    with pytest.raises(SystemExit, match="round does not exist: missing"):
        main(
            [
                "fleet-run",
                "--db",
                str(database_path),
                "--round",
                "missing",
                "--devices",
                str(config_path),
            ]
        )

    assert _schema_objects(database_path) == ()


def test_cli_fleet_run_rejects_a_device_mapping_mismatch_before_delegating(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.test_capacity import repository_with_round

    repository, round_id = repository_with_round(tmp_path)
    config_path = tmp_path / "devices.yaml"
    _write_fleet_config(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("phone-02", "phone-03"),
        encoding="utf-8",
    )
    delegated = False

    def fake_run(*args, **kwargs) -> int:
        nonlocal delegated
        delegated = True
        return 0

    monkeypatch.setattr(cli, "_run_acquisition_fleet", fake_run, raising=False)

    with pytest.raises(SystemExit, match="device ids do not match round"):
        main(
            [
                "fleet-run",
                "--db",
                str(repository.path),
                "--round",
                round_id,
                "--devices",
                str(config_path),
            ]
        )

    assert delegated is False


def test_cli_imports_comment_export_into_database(tmp_path: Path, capsys) -> None:
    source = tmp_path / "comments.csv"
    database_path = tmp_path / "tasks.db"
    source.write_text(
        HEADER
        + "1,comment_panel,744,https://video,707,sec,sample,Name,https://profile,,a,0,0,false,now\n",
        encoding="utf-8",
    )

    result = main(["import", str(source), "--db", str(database_path)])

    assert result == 0
    assert "imported=1 duplicates=0" in capsys.readouterr().out
    assert Database(database_path).count_by_state() == {"pending": 1}


def test_cli_validate_reports_real_target_count(tmp_path: Path, capsys) -> None:
    source = tmp_path / "comments.csv"
    source.write_text(
        HEADER
        + "1,comment_panel,744,https://video,707,sec,sample,Name,https://profile,,a,0,0,false,now\n",
        encoding="utf-8",
    )

    result = main(["validate", str(source)])

    assert result == 0
    assert "targets=1 duplicates=0" in capsys.readouterr().out


def test_cli_run_passes_interaction_limits_to_worker(
    tmp_path: Path, monkeypatch
) -> None:
    captured = {}

    def fake_run_queue(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(runner, "run_queue", fake_run_queue)

    result = main(
        [
            "run",
            "--db",
            str(tmp_path / "tasks.db"),
            "--like-probability",
            "0.5",
            "--like-hourly-limit",
            "8",
            "--favorite-probability",
            "1",
            "--favorite-hourly-limit",
            "4",
            "--share-probability",
            "0.25",
            "--share-hourly-limit",
            "2",
            "--device-id",
            "phone-01",
            "--event-driven",
        ]
    )

    assert result == 0
    policy = captured["interaction_policy"]
    assert policy.like.probability == 0.5
    assert policy.like.hourly_limit == 8
    assert policy.favorite.hourly_limit == 4
    assert policy.share.probability == 0.25
    assert captured["device_id"] == "phone-01"
    assert captured["event_driven"] is True


def test_cli_run_handles_keyboard_interrupt_without_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    def interrupted(*args, **kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_queue", interrupted)

    result = main(["run", "--db", str(tmp_path / "tasks.db")])

    assert result == 130


def _write_web_accounts(path: Path) -> None:
    path.write_text(
        """
accounts:
  - account_id: account-01
    device_id: phone-01
    business_id: business-01
    token_file: secrets/token.json
""".lstrip(),
        encoding="utf-8",
    )


def test_cli_dashboard_loads_web_account_and_webhook_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "web-accounts.yaml"
    _write_web_accounts(config_path)
    captured = {}

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    def fake_start_web_worker_thread(*args, **kwargs):
        captured["web_worker_args"] = args
        captured["web_worker_kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(
        web_worker,
        "start_web_worker_thread",
        fake_start_web_worker_thread,
        raising=False,
    )
    monkeypatch.setenv("TIKPOC_TIKTOK_APP_SECRET", "app-secret")
    monkeypatch.setenv("TIKPOC_WEBHOOK_MAX_AGE_SECONDS", "90")

    result = main(
        [
            "dashboard",
            "--db",
            str(tmp_path / "tasks.db"),
            "--web-accounts",
            str(config_path),
            "--with-web-worker",
            "--web-worker-idle-sleep",
            "0.5",
        ]
    )

    assert result == 0
    assert (
        captured["app"].state.registry.by_account_id("account-01").device_id
        == "phone-01"
    )
    assert captured["app"].state.tiktok_app_secret == "app-secret"
    assert captured["app"].state.webhook_max_age_seconds == 90
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["web_worker_args"][0] == tmp_path / "tasks.db"
    assert (
        captured["web_worker_kwargs"]["registry"].by_account_id("account-01").device_id
        == "phone-01"
    )
    assert captured["web_worker_kwargs"]["idle_sleep_seconds"] == 0.5


def test_cli_serve_starts_uvicorn_console(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    result = main(
        [
            "serve",
            "--db",
            str(tmp_path / "tasks.db"),
            "--port",
            "8877",
        ]
    )

    assert result == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8877
    assert captured["app"].state.database.path == tmp_path / "tasks.db"


def test_cli_serve_rejects_non_loopback_host(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "serve",
                "--db",
                str(tmp_path / "tasks.db"),
                "--host",
                "0.0.0.0",
            ]
        )


def test_cli_web_worker_passes_registry_and_once_flag(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "web-accounts.yaml"
    _write_web_accounts(config_path)
    captured = {}

    def fake_run_web_queue(*args, **kwargs) -> None:
        captured["args"] = args
        captured.update(kwargs)

    monkeypatch.setattr(web_worker, "run_web_queue", fake_run_web_queue, raising=False)

    result = main(
        [
            "web-worker",
            "--db",
            str(tmp_path / "tasks.db"),
            "--web-accounts",
            str(config_path),
            "--idle-sleep",
            "0.25",
            "--once",
        ]
    )

    assert result == 0
    assert captured["args"][0] == tmp_path / "tasks.db"
    assert captured["registry"].by_business_id("business-01").account_id == "account-01"
    assert captured["idle_sleep_seconds"] == 0.25
    assert captured["once"] is True


def test_cli_web_worker_loads_default_local_environment(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "web-accounts.yaml"
    _write_web_accounts(config_path)
    (tmp_path / ".env.local").write_text(
        "TKAUTO_LLM_BASE_URL=https://example.test/v1\n"
        "TKAUTO_LLM_API_KEY=local-key\n"
        "TKAUTO_LLM_MODEL=local-model\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run_web_queue(*args, **kwargs) -> None:
        captured["base_url"] = os.getenv("TKAUTO_LLM_BASE_URL")
        captured["api_key"] = os.getenv("TKAUTO_LLM_API_KEY")
        captured["model"] = os.getenv("TKAUTO_LLM_MODEL")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TKAUTO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TKAUTO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TKAUTO_LLM_MODEL", raising=False)
    monkeypatch.setattr(web_worker, "run_web_queue", fake_run_web_queue)

    assert (
        main(
            [
                "web-worker",
                "--db",
                str(tmp_path / "tasks.db"),
                "--web-accounts",
                str(config_path),
                "--once",
            ]
        )
        == 0
    )
    assert captured == {
        "base_url": "https://example.test/v1",
        "api_key": "local-key",
        "model": "local-model",
    }
