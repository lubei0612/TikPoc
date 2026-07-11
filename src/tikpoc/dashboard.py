import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import Database


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], database_path: Path) -> None:
        super().__init__(address, DashboardHandler)
        self.database = Database(database_path)
        self.database.migrate()


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            payload = self.server.database.dashboard_snapshot()
            payload["latest_event"] = self.server.database.latest_runtime_event()
            self._send_json(payload)
            return
        if parsed.path == "/api/recent":
            raw_limit = parse_qs(parsed.query).get("limit", ["10"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                limit = 10
            self._send_json(self.server.database.recent_tasks(limit))
            return
        static_files = {
            "/": ("dashboard.html", "text/html; charset=utf-8"),
            "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
            "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
        }
        if parsed.path in static_files:
            filename, content_type = static_files[parsed.path]
            self._send_file(filename, content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        action = self.path.removeprefix("/api/control/")
        states = {"pause": "paused", "resume": "running", "stop": "stopped"}
        if self.path.startswith("/api/control/") and action in states:
            state = states[action]
            self.server.database.set_worker_control(state)
            self._send_json({"control": state})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filename: str, content_type: str) -> None:
        body = (Path(__file__).parent / "static" / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(database_path: Path, host: str, port: int) -> DashboardServer:
    return DashboardServer((host, port), database_path)
