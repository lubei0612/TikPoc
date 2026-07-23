import json
from pathlib import Path
from urllib.error import URLError

import pytest

from tikpoc.importer import Target
from tikpoc.models import ProfileMetrics
from tikpoc.supabase_store import SupabaseBusinessStore


class FakeResponse:
    status = 201

    def close(self) -> None:
        pass


def test_supabase_pool_import_upserts_pool_and_chunked_targets() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    store = SupabaseBusinessStore(
        "https://project.supabase.co",
        "service-secret",
        opener=opener,
        batch_size=1,
    )
    targets = (
        Target(
            target_id="user-1",
            username="buyer_one",
            profile_url="https://www.tiktok.com/@buyer_one",
            source_video_id="",
            sec_uid="sec-1",
            profile_metrics=ProfileMetrics(20, 10, 5),
            private_account=False,
            identity_key="sec:sec-1",
            source_line_numbers=(2,),
        ),
        Target(
            target_id="user-2",
            username="buyer_two",
            profile_url="https://www.tiktok.com/@buyer_two",
            source_video_id="",
            sec_uid="sec-2",
            profile_metrics=ProfileMetrics(5, 15, 1),
            private_account=True,
            identity_key="sec:sec-2",
            source_line_numbers=(3,),
        ),
    )

    store.import_pool(
        pool_id="pool-abc",
        source_name="targets.csv",
        source_checksum="a" * 64,
        source_rows=2,
        targets=targets,
    )

    assert len(requests) == 5
    pool_request = requests[0][0]
    assert pool_request.full_url.endswith(
        "/rest/v1/tikpoc_target_pools?on_conflict=pool_id"
    )
    assert json.loads(pool_request.data) == [
        {
            "pool_id": "pool-abc",
            "source_name": "targets.csv",
            "source_checksum": "a" * 64,
            "source_rows": 0,
            "unique_targets": 0,
            "import_state": "importing",
        }
    ]
    delete_request = requests[1][0]
    assert delete_request.method == "DELETE"
    assert delete_request.full_url.endswith(
        "/rest/v1/tikpoc_targets?pool_id=eq.pool-abc"
    )
    target_rows = [json.loads(request.data)[0] for request, _timeout in requests[2:4]]
    assert [row["identity_key"] for row in target_rows] == ["sec:sec-1", "sec:sec-2"]
    assert target_rows[0]["following_count"] == 20
    assert target_rows[0]["follower_count"] == 10
    assert target_rows[1]["private_account"] is True
    completed_pool = json.loads(requests[4][0].data)
    assert completed_pool == [
        {
            "pool_id": "pool-abc",
            "source_name": "targets.csv",
            "source_checksum": "a" * 64,
            "source_rows": 2,
            "unique_targets": 2,
            "import_state": "complete",
        }
    ]
    for request, timeout in requests:
        assert request.headers["Authorization"] == "Bearer service-secret"
        assert timeout == 30.0


def test_supabase_pool_import_keeps_failed_batches_in_importing_state() -> None:
    requests = []
    fail_once = True

    def opener(request, *, timeout):
        nonlocal fail_once
        requests.append(request)
        if len(requests) == 3 and fail_once:
            fail_once = False
            raise URLError("private network detail")
        return FakeResponse()

    store = SupabaseBusinessStore(
        "https://project.supabase.co",
        "service-secret",
        opener=opener,
    )
    target = Target(
        target_id="user-1",
        username="buyer_one",
        profile_url="https://www.tiktok.com/@buyer_one",
        source_video_id="",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )

    with pytest.raises(RuntimeError, match="Supabase tikpoc_targets request failed"):
        store.import_pool(
            pool_id="pool-abc",
            source_name="targets.csv",
            source_checksum="a" * 64,
            source_rows=1,
            targets=(target,),
        )

    assert len(requests) == 3
    assert json.loads(requests[0].data)[0]["import_state"] == "importing"
    assert not any(
        request.data and json.loads(request.data)[0].get("import_state") == "complete"
        for request in requests
    )

    requests.clear()
    store.import_pool(
        pool_id="pool-abc",
        source_name="targets.csv",
        source_checksum="a" * 64,
        source_rows=1,
        targets=(target,),
    )

    assert requests[1].method == "DELETE"
    assert json.loads(requests[-1].data)[0]["import_state"] == "complete"


def test_supabase_store_loads_owner_only_env_without_exposing_key(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "supabase.env"
    env_file.write_text(
        "SUPABASE_URL=https://project.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=service-secret\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    store = SupabaseBusinessStore.from_env_file(env_file, opener=lambda *_a, **_k: None)

    assert store.url == "https://project.supabase.co"
    assert "service-secret" not in repr(store)


def test_supabase_store_upserts_accounts_and_device_health() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return FakeResponse()

    store = SupabaseBusinessStore(
        "https://project.supabase.co",
        "service-secret",
        opener=opener,
    )

    store.upsert_accounts(
        [
            {
                "account_id": "account-01",
                "device_id": "device-01",
                "expected_username": None,
                "browser_profile_label": "",
                "enabled": True,
                "browser_followback_enabled": False,
                "browser_dm_enabled": False,
            }
        ],
        observed_at="2026-07-19T12:00:00+00:00",
    )
    store.upsert_device_health(
        [
            {
                "device_id": "device-01",
                "account_id": "account-01",
                "state": "ready",
                "adb_state": "device",
                "tiktok_version": "44.8.42",
                "login_state": "logged_in",
                "proxy_state": "configured",
                "detail": {"myt_slot": 1},
            }
        ],
        observed_at="2026-07-19T12:00:00+00:00",
    )

    assert len(requests) == 2
    assert requests[0].full_url.endswith(
        "/rest/v1/tikpoc_accounts?on_conflict=account_id"
    )
    assert json.loads(requests[0].data) == [
        {
            "account_id": "account-01",
            "device_id": "device-01",
            "expected_username": None,
            "browser_profile_label": "",
            "enabled": True,
            "browser_followback_enabled": False,
            "browser_dm_enabled": False,
            "updated_at": "2026-07-19T12:00:00+00:00",
        }
    ]
    assert requests[1].full_url.endswith(
        "/rest/v1/tikpoc_device_health?on_conflict=device_id"
    )
    assert json.loads(requests[1].data)[0] == {
        "device_id": "device-01",
        "account_id": "account-01",
        "state": "ready",
        "adb_state": "device",
        "tiktok_version": "44.8.42",
        "login_state": "logged_in",
        "proxy_state": "configured",
        "detail": {"myt_slot": 1},
        "observed_at": "2026-07-19T12:00:00+00:00",
    }


def test_supabase_pool_import_rejects_an_empty_pool_before_writing() -> None:
    requests = []
    store = SupabaseBusinessStore(
        "https://project.supabase.co",
        "service-secret",
        opener=lambda request, **_kwargs: requests.append(request),
    )

    with pytest.raises(ValueError, match="target pool is empty"):
        store.import_pool(
            pool_id="pool-empty",
            source_name="empty.csv",
            source_checksum="a" * 64,
            source_rows=0,
            targets=(),
        )

    assert requests == []
