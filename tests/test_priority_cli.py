import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from tikpoc import priority_service
from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.cli import main
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round

from tests.test_cli import _write_fleet_config


def _target(name: str) -> Target:
    return Target(
        target_id=f"uid-{name}",
        username=name,
        profile_url=f"https://www.tiktok.com/@{name}",
        source_video_id="",
        sec_uid=f"sec-{name}",
        identity_key=f"sec:sec-{name}",
        source_line_numbers=(1,),
    )


def _seed_active_round(database: Path, *, checksum: str = "a") -> str:
    repository = AcquisitionRepository(database, clock_ms=lambda: 500)
    repository.migrate()
    pool = repository.import_pool(
        f"ordinary-{checksum}.csv", checksum * 64, (_target(f"ordinary-{checksum}"),)
    )
    return create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "ordinary-1", "phone-02": "ordinary-2"},
        starts_at_ms=100,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )


def _write_priority_jsonl(path: Path) -> None:
    rows = (
        {"username": "buyer.one", "source_live_id": "live-1"},
        {"username": "buyer.one", "source_live_id": "live-1"},
        {"username": "bad handle!", "source_live_id": "live-1"},
        {
            "username": "buyer.two",
            "sec_uid": "sec-buyer-two",
            "source_live_id": "live-1",
        },
    )
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_priority_import_is_idempotent_and_prints_redacted_json(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "tikpoc.db"
    parent_round = _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_priority_jsonl(source)
    command = [
        "priority-import",
        "--db",
        str(database),
        "--devices",
        str(devices),
        "--file",
        str(source),
        "--source-live",
        "live-1",
    ]

    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)

    assert first == second
    assert first == {
        "batch_id": first["batch_id"],
        "device_count": 2,
        "parent_round_id": parent_round,
        "skipped_duplicates": 1,
        "skipped_invalid": 1,
        "unique_targets": 2,
    }
    assert set(first) == {
        "batch_id",
        "device_count",
        "parent_round_id",
        "skipped_duplicates",
        "skipped_invalid",
        "unique_targets",
    }


def test_priority_status_prints_fifo_device_progress_and_parent_checkpoint(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "tikpoc.db"
    parent_round = _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_priority_jsonl(source)
    assert (
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-1",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["priority-status", "--db", str(database)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ordinary_checkpoint"] == {
        "completed": 0,
        "deferred": 0,
        "parent_round_id": parent_round,
        "pending": 2,
        "skipped": 0,
        "total": 2,
        "visits_confirmed": 0,
    }
    assert len(payload["batches"]) == 1
    batch = payload["batches"][0]
    assert batch["queue_sequence"] == 1
    assert batch["source_live_id"] == "live-1"
    assert batch["state"] == "queued"
    assert batch["devices"] == [
        {
            "completed": 0,
            "deferred": 0,
            "device_id": "phone-01",
            "pending": 2,
            "skipped": 0,
            "total": 2,
        },
        {
            "completed": 0,
            "deferred": 0,
            "device_id": "phone-02",
            "pending": 2,
            "skipped": 0,
            "total": 2,
        },
    ]


def test_priority_import_requires_exactly_one_active_ordinary_round(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database, checksum="a")
    _seed_active_round(database, checksum="b")
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_priority_jsonl(source)

    with pytest.raises(SystemExit, match="exactly one active ordinary round"):
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-1",
            ]
        )


def test_priority_import_replays_completed_batch_after_repository_restart(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "tikpoc.db"
    parent = _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_priority_jsonl(source)
    command = [
        "priority-import",
        "--db",
        str(database),
        "--devices",
        str(devices),
        "--file",
        str(source),
        "--source-live",
        "live-1",
    ]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    repository = AcquisitionRepository(database)
    batch = repository.priority_batch(first["batch_id"])
    with repository._connect() as connection:
        connection.execute(
            "UPDATE priority_batches SET state = 'completed' WHERE batch_id = ?",
            (batch.batch_id,),
        )
        connection.execute(
            "UPDATE exposure_rounds SET state = 'completed' WHERE round_id IN (?, ?)",
            (parent, batch.priority_round_id),
        )

    assert main(command) == 0
    replay = json.loads(capsys.readouterr().out)

    assert replay == first


def test_priority_import_accepts_valid_follower_workbook(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "followers.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(("follower_handle", "follower_uid", "follower_sec_uid"))
    sheet.append(("buyer.one", "dom-1-buyer.one", ""))
    workbook.save(source)
    workbook.close()

    assert (
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-workbook",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["unique_targets"] == 1


def test_priority_import_reports_corrupt_workbook_without_traceback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "broken.xlsx"
    source.write_bytes(b"not-a-workbook")

    with pytest.raises(SystemExit, match="priority workbook is invalid"):
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-workbook",
            ]
        )


def test_priority_import_reports_corrupt_worksheet_xml_without_traceback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    valid = tmp_path / "valid.xlsx"
    source = tmp_path / "broken-xml.xlsx"
    workbook = Workbook()
    workbook.active.append(("follower_handle", "follower_uid", "follower_sec_uid"))
    workbook.active.append(("buyer.one", "", ""))
    workbook.save(valid)
    workbook.close()
    with ZipFile(valid) as archive, ZipFile(source, "w", ZIP_DEFLATED) as broken:
        for name in archive.namelist():
            payload = archive.read(name)
            if name == "xl/worksheets/sheet1.xml":
                payload = b"<worksheet><broken>"
            broken.writestr(name, payload)

    with pytest.raises(SystemExit, match="priority workbook is invalid"):
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-workbook",
            ]
        )


def test_priority_import_rejects_source_changed_between_parse_and_checksum(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_priority_jsonl(source)
    original = priority_service.read_priority_targets

    def mutate_after_parse(path: Path, *, source_live_id: str):
        parsed = original(path, source_live_id=source_live_id)
        path.write_text(
            json.dumps({"username": "replacement", "source_live_id": source_live_id})
            + "\n",
            encoding="utf-8",
        )
        return parsed

    monkeypatch.setattr(priority_service, "read_priority_targets", mutate_after_parse)

    with pytest.raises(SystemExit, match="priority input changed while reading"):
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-1",
            ]
        )


def test_priority_import_rechecks_unique_parent_inside_create_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database)
    devices = tmp_path / "devices.yaml"
    _write_fleet_config(devices)
    source = tmp_path / "live.jsonl"
    _write_priority_jsonl(source)
    original = AcquisitionRepository.create_priority_batch
    injected = False

    def create_after_competing_round(self, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            pool = self.import_pool("competing.csv", "e" * 64, (_target("competing"),))
            create_exposure_round(
                self,
                pool_id=pool.pool_id,
                device_seeds={"phone-01": "competing-1", "phone-02": "competing-2"},
                starts_at_ms=100,
                min_inter_device_gap_ms=0,
                min_repeat_gap_ms=0,
            )
        return original(self, **kwargs)

    monkeypatch.setattr(
        AcquisitionRepository, "create_priority_batch", create_after_competing_round
    )

    with pytest.raises(SystemExit, match="exactly one active ordinary round"):
        main(
            [
                "priority-import",
                "--db",
                str(database),
                "--devices",
                str(devices),
                "--file",
                str(source),
                "--source-live",
                "live-1",
            ]
        )


def test_priority_status_rejects_multiple_active_ordinary_rounds(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tikpoc.db"
    _seed_active_round(database, checksum="a")
    _seed_active_round(database, checksum="b")

    with pytest.raises(SystemExit, match="multiple active ordinary rounds"):
        main(["priority-status", "--db", str(database)])
