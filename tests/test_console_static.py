import re
from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.api import create_app


def _console_asset(client: TestClient, html: str) -> str:
    match = re.search(r'(?:src|href)="(/console-assets/[^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_console_index_and_assets_are_served(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "tikpoc.db"))

    for route in ("/", "/operations", "/inbox", "/analytics"):
        response = client.get(route)
        assert response.status_code == 200
        assert '<div id="root"></div>' in response.text
        assert response.headers["cache-control"] == "no-cache"

    asset_path = _console_asset(client, client.get("/operations").text)
    asset = client.get(asset_path)
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_console_assets_only_serve_hashed_build_files(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "tikpoc.db"))
    asset_path = _console_asset(client, client.get("/").text)

    assert re.fullmatch(
        r"/console-assets/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]+\.(?:js|css)", asset_path
    )
    assert client.get("/console-assets/index.html").status_code == 404
    assert client.get("/console-assets/../dashboard.html").status_code == 404
    assert client.get("/console-assets/%2e%2e/dashboard.html").status_code == 404


def test_legacy_dashboard_assets_remain_available(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "tikpoc.db"))

    assert client.get("/dashboard.css").status_code == 200
    assert client.get("/dashboard.js").status_code == 200
    assert client.get("/api/status").status_code == 200
