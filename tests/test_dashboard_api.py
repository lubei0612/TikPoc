import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tikpoc.dashboard import create_server
from tikpoc.db import Database


def _start_server(database_path: Path):
    server = create_server(database_path, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_status_recent_and_pause_api(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    database = Database(database_path)
    database.migrate()
    database.insert_task("batch", "1", "sample")
    server, base_url = _start_server(database_path)
    try:
        status = json.load(urlopen(f"{base_url}/api/status"))
        recent = json.load(urlopen(f"{base_url}/api/recent?limit=10"))
        response = urlopen(Request(f"{base_url}/api/control/pause", method="POST"))

        assert status["total"] == 1
        assert status["counts"] == {"pending": 1}
        assert recent == []
        assert json.load(response)["control"] == "paused"
        assert database.worker_control() == "paused"
    finally:
        server.shutdown()


def test_unknown_route_returns_404(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server, base_url = _start_server(database_path)
    try:
        try:
            urlopen(f"{base_url}/missing")
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("missing route returned success")
    finally:
        server.shutdown()
