import sqlite3
from pathlib import Path

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import OutcomeKind


def test_pacing_state_refills_to_capacity_and_consumes_once(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()

    initial = repository.action_pacing_state(
        "phone-01", OutcomeKind.LIKE, now_ms=0, limit=100
    )
    repeated = repository.action_pacing_state(
        "phone-01", OutcomeKind.LIKE, now_ms=0, limit=100
    )
    full = repository.action_pacing_state(
        "phone-01", OutcomeKind.LIKE, now_ms=36_000, limit=100
    )

    assert 0 <= initial.tokens < 1
    assert repeated == initial
    assert full.tokens == 1
    assert full.ready is True
    assert repository.consume_action_token(
        "phone-01", OutcomeKind.LIKE, now_ms=36_000, limit=100
    )
    assert not repository.consume_action_token(
        "phone-01", OutcomeKind.LIKE, now_ms=36_000, limit=100
    )


def test_rolling_usage_counts_only_current_non_trace_reservations(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    with sqlite3.connect(repository.path) as connection:
        for index, (outcome, created_at_ms) in enumerate(
            (("like", 1_000), ("like", 3_600_001), ("trace", 3_600_001)),
            start=1,
        ):
            connection.execute(
                """
                INSERT INTO device_action_plans(
                    round_id, identity_key, device_id, seed,
                    requested_outcome, effective_outcome, state, created_at_ms
                ) VALUES (?, ?, 'phone-01', ?, ?, ?, 'confirmed', ?)
                """,
                (
                    f"round-{index}",
                    f"sec:{index}",
                    f"{index:064x}",
                    outcome,
                    outcome,
                    created_at_ms,
                ),
            )

    assert (
        repository.rolling_action_usage("phone-01", OutcomeKind.LIKE, now_ms=3_600_001)
        == 2
    )
    assert (
        repository.rolling_action_usage("phone-01", OutcomeKind.LIKE, now_ms=3_601_001)
        == 1
    )
    assert (
        repository.rolling_action_usage("phone-01", OutcomeKind.TRACE, now_ms=3_601_001)
        == 0
    )


def test_pacing_state_rejects_time_reversal_and_invalid_limits(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    repository.action_pacing_state(
        "phone-01", OutcomeKind.FAVORITE, now_ms=100, limit=14
    )

    for now_ms, limit in ((99, 14), (100, 0)):
        try:
            repository.action_pacing_state(
                "phone-01", OutcomeKind.FAVORITE, now_ms=now_ms, limit=limit
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid pacing state was accepted")
