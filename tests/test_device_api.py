import sqlite3
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository


def repository(tmp_path: Path) -> AcquisitionRepository:
    tokens = iter(("token-one", "token-two", "token-three"))
    result = AcquisitionRepository(
        tmp_path / "acquisition.db", token_factory=tokens.__next__
    )
    result.migrate()
    return result


def test_register_device_rotates_session_epoch_and_revokes_old_token(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)

    first = repo.register_mobile_device("device-1", "account-1", now_ms=1_000)
    second = repo.register_mobile_device("device-1", "account-1", now_ms=2_000)

    assert (first.session_epoch, first.access_token) == (1, "token-one")
    assert (second.session_epoch, second.access_token) == (2, "token-two")
    assert (
        repo.authenticate_mobile_device("device-1", "token-one", now_ms=3_000) is None
    )
    authenticated = repo.authenticate_mobile_device(
        "device-1", "token-two", now_ms=3_000
    )
    assert authenticated is not None
    assert authenticated.device_id == "device-1"
    assert authenticated.account_id == "account-1"
    assert authenticated.session_epoch == 2
    assert authenticated.access_token == ""


def test_registration_persists_only_token_digest(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    session = repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    with sqlite3.connect(repo.path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM mobile_devices WHERE device_id = 'device-1'"
        ).fetchone()

    assert row is not None
    assert "access_token" not in row
    assert row["token_digest"] != session.access_token
    assert len(row["token_digest"]) == 64


def test_register_rejects_account_binding_mismatch(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    with pytest.raises(ValueError, match="mobile device binding mismatch"):
        repo.register_mobile_device("device-1", "account-2", now_ms=2_000)


def test_revoked_device_does_not_authenticate(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    session = repo.register_mobile_device("device-1", "account-1", now_ms=1_000)

    repo.revoke_mobile_device("device-1", now_ms=2_000)

    assert (
        repo.authenticate_mobile_device("device-1", session.access_token, now_ms=3_000)
        is None
    )
