import os
from pathlib import Path

from tikpoc import dashboard, runner, web_worker
from tikpoc.cli import main
from tikpoc.db import Database

from tests.test_importer import HEADER


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


def test_cli_run_passes_interaction_limits_to_worker(tmp_path: Path, monkeypatch) -> None:
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

    class FakeServer:
        server_port = 8765

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            captured["closed"] = True

    def fake_create_server(*args, **kwargs):
        captured.update(kwargs)
        return FakeServer()

    def fake_start_web_worker_thread(*args, **kwargs):
        captured["web_worker_args"] = args
        captured["web_worker_kwargs"] = kwargs

    monkeypatch.setattr(dashboard, "create_server", fake_create_server)
    monkeypatch.setattr(
        web_worker, "start_web_worker_thread", fake_start_web_worker_thread, raising=False
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
    assert captured["web_account_registry"].by_account_id(
        "account-01"
    ).device_id == "phone-01"
    assert captured["tiktok_app_secret"] == "app-secret"
    assert captured["webhook_max_age_seconds"] == 90
    assert captured["web_worker_args"][0] == tmp_path / "tasks.db"
    assert captured["web_worker_kwargs"]["registry"].by_account_id(
        "account-01"
    ).device_id == "phone-01"
    assert captured["web_worker_kwargs"]["idle_sleep_seconds"] == 0.5
    assert captured["closed"] is True


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
    assert captured["registry"].by_business_id(
        "business-01"
    ).account_id == "account-01"
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
