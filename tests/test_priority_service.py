import json
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.fleet import FleetConfig
from tikpoc.priority_service import PriorityBatchService

from tests.test_cli import _write_fleet_config
from tests.test_priority_cli import _seed_active_round


def _write_live_file(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "username": "buyer.one",
                "sec_uid": "sec-buyer-one",
                "source_live_id": "live-active",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _set_control(
    repository: AcquisitionRepository, device_id: str, state: str, *, stamp: int
) -> None:
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO operator_control_states(
                scope, scope_id, state, updated_at_ms, command_id
            ) VALUES ('device', ?, ?, ?, ?)
            ON CONFLICT(scope, scope_id) DO UPDATE SET
                state=excluded.state,
                updated_at_ms=excluded.updated_at_ms,
                command_id=excluded.command_id
            """,
            (device_id, state, stamp, f"control-{device_id}-{stamp}"),
        )


def test_live_import_snapshots_running_devices_and_replay_keeps_participants(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    repository = AcquisitionRepository(database, clock_ms=lambda: 500)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_live_file(source)
    _set_control(repository, "phone-01", "running", stamp=1)
    _set_control(repository, "phone-02", "paused", stamp=2)
    service = PriorityBatchService(repository)

    first = service.import_batch(
        source,
        source_live_id="live-active",
        fleet_config=FleetConfig.from_path(devices),
    )
    _set_control(repository, "phone-01", "paused", stamp=3)
    _set_control(repository, "phone-02", "running", stamp=4)
    replay = service.import_batch(
        source,
        source_live_id="live-active",
        fleet_config=FleetConfig.from_path(devices),
    )

    assert first == replay
    assert first.device_count == 1
    assert repository.priority_batch_device_ids(first.batch_id) == ("phone-01",)


def test_live_import_rejects_when_every_parent_device_is_paused(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    repository = AcquisitionRepository(database, clock_ms=lambda: 500)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_live_file(source)
    _set_control(repository, "phone-01", "paused", stamp=1)
    _set_control(repository, "phone-02", "paused", stamp=2)

    with pytest.raises(ValueError, match="running device"):
        PriorityBatchService(repository).import_batch(
            source,
            source_live_id="live-active",
            fleet_config=FleetConfig.from_path(devices),
        )
