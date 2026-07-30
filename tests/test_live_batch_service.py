from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.live_batch_service import LiveBatchService, LiveTargetInput


def repository_with_host(tmp_path: Path) -> tuple[AcquisitionRepository, str]:
    repository = AcquisitionRepository(tmp_path / "live.db", clock_ms=lambda: 500)
    repository.migrate()
    host = repository.ensure_live_host_round(
        host_id="main",
        device_seeds={"d1": "host-d1", "d2": "host-d2"},
        now_ms=100,
    )
    return repository, host


def set_control(repository: AcquisitionRepository, device_id: str, state: str) -> None:
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO operator_control_states(
                scope, scope_id, state, updated_at_ms, command_id
            ) VALUES ('device', ?, ?, 200, ?)
            """,
            (device_id, state, f"control-{device_id}"),
        )


def test_submit_normalizes_deduplicates_and_snapshots_running_devices(
    tmp_path: Path,
) -> None:
    repository, host = repository_with_host(tmp_path)
    set_control(repository, "d2", "paused")
    service = LiveBatchService(repository)
    rows = (
        LiveTargetInput("@Buyer.One", sec_uid="sec-one", collected_at_ms=100),
        LiveTargetInput("buyer.one", sec_uid="sec-one", collected_at_ms=101),
        LiveTargetInput("bad handle!"),
        LiveTargetInput("buyer.two", uid="uid-two", source_video_id="video-2"),
    )

    summary = service.submit(
        host_round_id=host,
        source_live_id="live-123",
        targets=rows,
    )
    replay = service.submit(
        host_round_id=host,
        source_live_id="live-123",
        targets=tuple(reversed(rows)),
    )

    assert replay == summary
    assert summary.unique_targets == 2
    assert summary.skipped_duplicates == 1
    assert summary.skipped_invalid == 1
    assert summary.device_count == 1
    assert summary.navigation_mode == "deeplink"
    assert repository.priority_batch_device_ids(summary.batch_id) == ("d1",)
    batch = repository.priority_batch(summary.batch_id)
    targets = repository.pool_targets(batch.pool_id)
    assert [target.identity_key for target in targets] == [
        "sec:sec-one",
        "uid:uid-two",
    ]
    assert targets[0].username == "buyer.one"


def test_submit_rejects_identity_conflict_and_no_running_device(
    tmp_path: Path,
) -> None:
    repository, host = repository_with_host(tmp_path)
    service = LiveBatchService(repository)

    with pytest.raises(ValueError, match="conflicting identity"):
        service.submit(
            host_round_id=host,
            source_live_id="live-conflict",
            targets=(
                LiveTargetInput("buyer.one", sec_uid="sec-one"),
                LiveTargetInput("buyer.one", sec_uid="sec-other"),
            ),
        )

    set_control(repository, "d1", "paused")
    set_control(repository, "d2", "paused")
    with pytest.raises(ValueError, match="running device"):
        service.submit(
            host_round_id=host,
            source_live_id="live-paused",
            targets=(LiveTargetInput("buyer.one"),),
        )


def test_submit_rejects_empty_or_invalid_batch(tmp_path: Path) -> None:
    repository, host = repository_with_host(tmp_path)
    service = LiveBatchService(repository)

    with pytest.raises(ValueError, match="target"):
        service.submit(
            host_round_id=host,
            source_live_id="live-empty",
            targets=(LiveTargetInput("bad handle!"),),
        )
