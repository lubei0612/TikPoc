import sqlite3
from collections import Counter
from dataclasses import FrozenInstanceError, replace
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
    assert first.created["rounds"] == 3
    assert first.created["device_seeds"] == 9
    assert first.created["assignments"] == 36
    assert first.created["confirmed_visits"] == 31
    assert first.created["action_plans"] == 31
    assert first.created["interaction_plans"] == 6
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
        historical_rounds = connection.execute(
            """
            SELECT round_id, state FROM exposure_rounds
            WHERE round_id LIKE 'demo-round-ai-growth-history-%'
            ORDER BY round_id
            """
        ).fetchall()
        assert [tuple(row) for row in historical_rounds] == [
            ("demo-round-ai-growth-history-01", "completed"),
            ("demo-round-ai-growth-history-02", "completed"),
        ]
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM round_device_seeds
            WHERE round_id LIKE 'demo-round-ai-growth-history-%'
            """
            ).fetchone()[0]
            == 6
        )
        outcomes = dict(
            connection.execute(
                """
                SELECT effective_outcome, COUNT(*) AS count FROM (
                    SELECT effective_outcome FROM device_action_plans
                    WHERE round_id = ? ORDER BY plan_id LIMIT 6
                )
                GROUP BY effective_outcome
                """,
                (blueprint.round_id,),
            )
        )
        assert outcomes == {"favorite": 2, "like": 2, "repost": 1, "trace": 1}
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM assignment_phase_history AS history
                JOIN round_assignments AS assignment
                  ON assignment.assignment_id = history.assignment_id
                WHERE history.to_phase = 'profile_opening'
                  AND assignment.round_id = ?
                """,
                (blueprint.round_id,),
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
                  AND assignment.completed_at_ms IS NOT NULL
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


def test_seed_acquisition_keeps_main_round_evidence_chronological(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )
    seed_demo_database(path, blueprint)

    with sqlite3.connect(path) as connection:
        starts_at_ms = connection.execute(
            "SELECT starts_at_ms FROM exposure_rounds WHERE round_id = ?",
            (blueprint.round_id,),
        ).fetchone()[0]
        bounds = connection.execute(
            """
            SELECT MIN(history.changed_at_ms), MAX(history.changed_at_ms),
                   MIN(assignment.visit_confirmed_at_ms),
                   MAX(assignment.completed_at_ms)
            FROM round_assignments AS assignment
            JOIN assignment_phase_history AS history
              ON history.assignment_id = assignment.assignment_id
            WHERE assignment.round_id = ?
            """,
            (blueprint.round_id,),
        ).fetchone()
        assert all(starts_at_ms <= value <= blueprint.now_ms for value in bounds)
        assert (
            connection.execute(
                """
                SELECT COUNT(*)
                FROM device_action_plans AS plan
                JOIN profile_snapshots AS snapshot
                  ON snapshot.round_id = plan.round_id
                 AND snapshot.identity_key = plan.identity_key
                LEFT JOIN action_attempts AS attempt ON attempt.plan_id = plan.plan_id
                WHERE plan.round_id = ? AND (
                    snapshot.observed_at_ms > plan.created_at_ms
                    OR attempt.attempted_at_ms < plan.created_at_ms
                    OR plan.created_at_ms NOT BETWEEN ? AND ?
                )
                """,
                (blueprint.round_id, starts_at_ms, blueprint.now_ms),
            ).fetchone()[0]
            == 0
        )


def test_seed_acquisition_provides_valid_capacity_domain_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )
    seed_demo_database(path, blueprint)

    audit = AcquisitionRepository(path).capacity_audit(
        blueprint.round_id,
        expected_devices=blueprint.scale.devices,
    )

    assert len(audit.timings) == blueprint.metrics.confirmed_visits - 1
    assert audit.false_success_count == 0
    assert audit.quota_overrun_count == 0
    assert audit.uncertain_count == 1
    assert audit.deferred_count == 1


def test_historical_rounds_have_coherent_nonempty_completed_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )
    seed_demo_database(path, blueprint)
    repository = AcquisitionRepository(path)

    for round_id in demo_data.DEMO_HISTORY_ROUND_IDS:
        assert repository.assignment_count(round_id) == blueprint.scale.devices
        assert repository.round_coverage(round_id) == {
            "round_id": round_id,
            "targets": 1,
            "required_devices": blueprint.scale.devices,
            "confirmed_visits": blueprint.scale.devices,
            "fully_covered": 1,
            "coverage_rate": 1.0,
        }
        audit = repository.capacity_audit(
            round_id,
            expected_devices=blueprint.scale.devices,
        )
        assert len(audit.timings) == blueprint.scale.devices
        assert audit.false_success_count == 0
        assert audit.quota_overrun_count == 0
        with sqlite3.connect(path) as connection:
            target_count = connection.execute(
                """
                SELECT pool.unique_targets
                FROM exposure_rounds AS round
                JOIN target_pools AS pool ON pool.pool_id = round.pool_id
                WHERE round.round_id = ?
                """,
                (round_id,),
            ).fetchone()[0]
        assert target_count == 1


def test_uncertain_plans_are_nontrace_featured_attempts_with_deferred_assignments(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    scale = replace(DemoScale.test_fixture(), ai_uncertain=5)
    blueprint = build_demo_blueprint(now_ms=NOW_MS, scale=scale)
    seed_demo_database(path, blueprint)

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT plan.effective_outcome, attempt.result, assignment.phase,
                   assignment.visit_confirmed_at_ms, assignment.completed_at_ms
            FROM device_action_plans AS plan
            JOIN round_assignments AS assignment
              ON assignment.round_id = plan.round_id
             AND assignment.identity_key = plan.identity_key
             AND assignment.device_id = plan.device_id
            LEFT JOIN action_attempts AS attempt ON attempt.plan_id = plan.plan_id
            WHERE plan.round_id = ? AND plan.state = 'uncertain'
            ORDER BY plan.plan_id
            """,
            (blueprint.round_id,),
        ).fetchall()

    assert len(rows) == 5
    assert all(outcome != "trace" for outcome, *_rest in rows)
    assert all(attempt == "uncertain" for _outcome, attempt, *_rest in rows)
    assert all(phase == "deferred" for _outcome, _attempt, phase, *_rest in rows)
    assert all(visit is not None for *_prefix, visit, _completed in rows)
    assert all(completed is None for *_prefix, completed in rows)


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
