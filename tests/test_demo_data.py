import sqlite3
from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tikpoc import demo_data
from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.demo_data import (
    DemoScale,
    build_demo_blueprint,
    seed_demo_database,
)

NOW_MS = 1_788_499_200_000


def test_default_blueprint_matches_portfolio_contract() -> None:
    blueprint = build_demo_blueprint(now_ms=NOW_MS)

    assert blueprint.namespace == "demo-ai-growth-v1"
    assert blueprint.pool_id == "demo-pool-ai-growth-v1"
    assert blueprint.round_id == "demo-round-ai-growth-v1"
    assert blueprint.label == "DEMO · AI 多账号获客转化试点"
    assert len(blueprint.targets) == 10_000
    assert len(blueprint.accounts) == 7
    assert blueprint.metrics.assignments == 70_000
    assert blueprint.metrics.confirmed_visits == 68_420
    assert blueprint.metrics.fully_covered == 9_770
    assert blueprint.metrics.eligible == 5_860
    assert blueprint.metrics.interactions == 4_410
    assert blueprint.metrics.followers == 1_240
    assert blueprint.metrics.inbound == 486
    assert blueprint.metrics.engaged == 326
    assert blueprint.metrics.qualified == 173
    assert blueprint.metrics.invited == 126
    assert blueprint.metrics.contact_captured == 72
    assert blueprint.metrics.human_required == 28
    assert blueprint.metrics.sales == 19
    assert blueprint.metrics.ai_plans == 348
    assert blueprint.metrics.ai_sent == 331
    assert blueprint.metrics.ai_uncertain == 5
    assert blueprint.metrics.ai_superseded == 12
    assert len(blueprint.timeline) == 14


def test_blueprint_has_unique_synthetic_account_and_device_mappings() -> None:
    accounts = build_demo_blueprint(now_ms=NOW_MS).accounts

    assert [item.account_id for item in accounts] == [
        f"demo-account-{index:02d}" for index in range(1, 8)
    ]
    assert [item.device_id for item in accounts] == [
        f"demo-device-{index:02d}" for index in range(1, 8)
    ]
    assert [item.profile_label for item in accounts] == [
        f"DEMO Profile {index:02d}" for index in range(1, 8)
    ]
    assert [item.username for item in accounts] == [
        f"demo_shop_{index:02d}" for index in range(1, 8)
    ]
    assert all(
        item.private_channel_hint == f"https://example.invalid/demo-channel/{index:02d}"
        for index, item in enumerate(accounts, start=1)
    )


def test_blueprint_is_deterministic_for_same_clock_and_scale() -> None:
    scale = DemoScale.portfolio()

    first = build_demo_blueprint(now_ms=NOW_MS, scale=scale)
    second = build_demo_blueprint(now_ms=NOW_MS, scale=scale)

    assert first == second


def test_test_fixture_uses_small_contract_and_derived_timestamps() -> None:
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )

    assert len(blueprint.targets) == 12
    assert len(blueprint.accounts) == 3
    assert blueprint.metrics.assignments == 36
    assert blueprint.metrics.confirmed_visits == 31
    assert blueprint.metrics.fully_covered == 10
    assert len(blueprint.conversations) == 8
    assert blueprint.metrics.sales == 3
    assert blueprint.timeline[-1].started_at_ms <= NOW_MS
    assert all(day.started_at_ms <= NOW_MS for day in blueprint.timeline)
    assert all(item.occurred_at_ms <= NOW_MS for item in blueprint.conversations)


def test_blueprint_types_are_immutable() -> None:
    blueprint = build_demo_blueprint(now_ms=NOW_MS)

    with pytest.raises(FrozenInstanceError):
        blueprint.namespace = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        blueprint.metrics.sales = 0  # type: ignore[misc]


def test_blueprint_rejects_nonpositive_clock() -> None:
    with pytest.raises(ValueError, match="demo clock must be positive"):
        build_demo_blueprint(now_ms=0)


def test_seed_acquisition_is_idempotent_and_matches_small_scale(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )

    first = seed_demo_database(path, blueprint)
    second = seed_demo_database(path, blueprint)
    repository = AcquisitionRepository(path)

    assert first.created["pools"] == 1
    assert first.created["targets"] == 12
    assert first.created["rounds"] == 1
    assert first.created["device_seeds"] == 3
    assert first.created["assignments"] == 36
    assert first.created["confirmed_visits"] == 31
    assert first.created["action_plans"] == 6
    assert first.created["uncertain_action_plans"] == 1
    assert first.created["device_health"] == 3
    assert second.created_total == 0
    assert repository.assignment_count(blueprint.round_id) == 36
    coverage = repository.round_coverage(blueprint.round_id)
    assert coverage == {
        "round_id": blueprint.round_id,
        "targets": 12,
        "required_devices": 3,
        "confirmed_visits": 31,
        "fully_covered": 10,
        "coverage_rate": 10 / 12,
    }

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        outcomes = dict(
            connection.execute(
                """
                SELECT effective_outcome, COUNT(*) AS count
                FROM device_action_plans
                WHERE round_id = ?
                GROUP BY effective_outcome
                """,
                (blueprint.round_id,),
            )
        )
        assert outcomes == {"favorite": 2, "like": 2, "repost": 1, "trace": 1}
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assignment_phase_history"
            ).fetchone()[0]
            == 31
        )
        durations = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT assignment.completed_at_ms - history.changed_at_ms AS duration_ms
                FROM round_assignments AS assignment
                JOIN assignment_phase_history AS history
                  ON history.assignment_id = assignment.assignment_id
                WHERE assignment.round_id = ?
                  AND history.to_phase = 'profile_opening'
                ORDER BY duration_ms
                """,
                (blueprint.round_id,),
            )
        ]
        assert durations and min(durations) > 0
        assert sum(durations) / len(durations) < 6_500
        assert durations[int(len(durations) * 0.9)] < 8_640
        health = connection.execute(
            """
            SELECT state, COUNT(*) FROM fleet_device_health
            WHERE device_id LIKE 'demo-device-%' GROUP BY state
            """
        ).fetchall()
        assert dict(health) == {"healthy": 2, "unhealthy": 1}
        controls = connection.execute(
            """
            SELECT state, COUNT(*) FROM operator_control_states
            WHERE scope = 'device' AND scope_id LIKE 'demo-device-%'
            GROUP BY state
            """
        ).fetchall()
        assert dict(controls) == {"stopped": 3}
        assert (
            connection.execute("SELECT COUNT(*) FROM device_worker_leases").fetchone()[
                0
            ]
            == 0
        )
        assert (
            connection.execute(
                "SELECT requested_state FROM worker_control WHERE singleton = 1"
            ).fetchone()[0]
            == "stopped"
        )


def test_seed_acquisition_rolls_back_when_later_seed_phase_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tikpoc.db"
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )

    def fail_conversion(
        connection: sqlite3.Connection, selected: object
    ) -> dict[str, int]:
        del connection, selected
        raise RuntimeError("conversion seed failed")

    monkeypatch.setattr(demo_data, "_seed_conversion", fail_conversion)

    with pytest.raises(RuntimeError, match="conversion seed failed"):
        seed_demo_database(path, blueprint)

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pool_targets WHERE identity_key LIKE 'demo:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM exposure_rounds WHERE round_id LIKE 'demo-%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM round_assignments WHERE identity_key LIKE 'demo:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM fleet_device_health WHERE device_id LIKE 'demo-%'"
            ).fetchone()[0]
            == 0
        )


def test_portfolio_confirmed_projection_has_exact_coverage_distribution() -> None:
    blueprint = build_demo_blueprint(now_ms=NOW_MS)

    keys = demo_data._confirmed_assignment_keys(blueprint)
    coverage = Counter(identity_key for identity_key, _device_id in keys)

    assert len(keys) == 68_420
    assert sum(count == 7 for count in coverage.values()) == 9_770
    assert sum(count < 7 for count in coverage.values()) == 30
