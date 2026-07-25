from __future__ import annotations

import json
from pathlib import Path

import pytest

from tikpoc.publishing_db import PublishingJob
from tikpoc.roxy_publishing import (
    RoxyApiClient,
    expected_username_from_profile_url,
    photo_title,
    validate_visible_username,
)


def _job() -> PublishingJob:
    return PublishingJob(
        job_id=1,
        source_key="gxhy:shop:product",
        account_id="account-01",
        caption=(
            "A versatile gray-toned monogram-style clutch bag with a 30 × 20 cm "
            "profile, designed for everyday essentials. Interested in this style? "
            "See our profile link or pinned post for details."
        ),
        asset_paths=("/tmp/one.jpg",),
        asset_sha256s=("0" * 64,),
        state="prepared",
        lease_owner="",
        lease_expires_at_ms=0,
        visible_post_url="",
        created_at_ms=100,
        updated_at_ms=100,
    )


def test_roxy_api_client_redacts_key_and_parses_connections() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "code": 0,
                    "data": [
                        {
                            "windowName": "IKUN-ikun.bags4",
                            "dirId": "dir-01",
                            "http": "127.0.0.1:50050",
                            "driver": "/driver",
                        }
                    ],
                }
            ).encode()

    seen = {}

    def opener(request, timeout):
        seen["token"] = request.headers["Token"]
        seen["timeout"] = timeout
        return Response()

    client = RoxyApiClient(
        api_key="secret", api_host="http://127.0.0.1:50000", opener=opener
    )

    connections = client.connections()

    assert connections[0].profile_name == "IKUN-ikun.bags4"
    assert connections[0].debugger_address == "127.0.0.1:50050"
    assert seen == {"token": "secret", "timeout": 30}
    assert "secret" not in repr(client)


def test_profile_url_identity_is_normalized_and_mismatch_stops() -> None:
    assert (
        expected_username_from_profile_url("https://www.tiktok.com/@IKUN.Bags4")
        == "ikun.bags4"
    )
    validate_visible_username("IKUN.Bags4", "@ikun.bags4")
    with pytest.raises(ValueError, match="identity mismatch"):
        validate_visible_username("ikun.bags4", "ikun.bags6")


def test_photo_title_is_short_and_excludes_contact_call_to_action() -> None:
    title = photo_title(_job())

    assert len(title) <= 90
    assert title.startswith("A versatile gray-toned")
    assert "profile link" not in title


def test_roxy_env_file_requires_key(tmp_path: Path) -> None:
    path = tmp_path / "roxy.env"
    path.write_text("ROXY_API_HOST=http://127.0.0.1:50000\n")

    with pytest.raises(ValueError, match="ROXY_API_KEY"):
        RoxyApiClient.from_env_file(path)
