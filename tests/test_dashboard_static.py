import threading
from pathlib import Path
from urllib.request import urlopen

from tikpoc.dashboard import create_server
from tikpoc.db import Database


def test_dashboard_static_routes_include_operational_controls(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server = create_server(database_path, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        html = urlopen(f"{base_url}/").read().decode()
        css = urlopen(f"{base_url}/dashboard.css").read().decode()
        javascript = urlopen(f"{base_url}/dashboard.js").read().decode()

        for element_id in (
            "progressBar",
            "processedCount",
            "workerState",
            "pauseButton",
            "resumeButton",
            "stopButton",
            "recentTasks",
        ):
            assert f'id="{element_id}"' in html
        assert "max-width: 520px" in css
        assert "setInterval" in javascript
        assert "/api/status" in javascript
    finally:
        server.shutdown()
