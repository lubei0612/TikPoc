import json
import math
import sqlite3
from collections import Counter
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType

import pytest

from tikpoc import demo_data
from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.db import Database
from tikpoc.demo_data import (
    DemoScale,
    build_demo_blueprint,
    clear_demo_database,
    seed_demo_database,
)
from tikpoc.runtime_settings import RuntimeSettingsStore
from tikpoc.web_accounts import WebAccountRegistry

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
    assert "interaction_plans" not in first.created
    assert "uncertain_action_plans" not in first.created
    assert first.summary["interaction_plans"] == 6
    assert first.summary["uncertain_action_plans"] == 1
    assert first.created["device_health"] == 3
    assert first.created_total == sum(first.created.values())
    assert isinstance(first.created, MappingProxyType)
    assert isinstance(first.summary, MappingProxyType)
    with pytest.raises(TypeError):
        first.created["targets"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        first.summary["interaction_plans"] = 0  # type: ignore[index]
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
        assert outcomes == {"favorite": 2, "like": 2, "repost": 2}
        persisted_counts = connection.execute(
            """
            SELECT SUM(effective_outcome <> 'trace'),
                   SUM(effective_outcome = 'trace'),
                   (SELECT COUNT(*) FROM action_attempts AS attempt
                    JOIN device_action_plans AS attempted
                      ON attempted.plan_id = attempt.plan_id
                    WHERE attempted.round_id = ?)
            FROM device_action_plans WHERE round_id = ?
            """,
            (blueprint.round_id, blueprint.round_id),
        ).fetchone()
        assert tuple(persisted_counts) == (6, 25, 6)
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
        nearest_rank_p90 = durations[max(0, math.ceil(len(durations) * 0.9) - 1)]
        assert nearest_rank_p90 < 8_640
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


def test_portfolio_seed_persists_exact_interaction_and_trace_counts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "portfolio.db"
    blueprint = build_demo_blueprint(now_ms=NOW_MS)

    result = seed_demo_database(path, blueprint)

    with sqlite3.connect(path) as connection:
        counts = connection.execute(
            """
            SELECT SUM(effective_outcome <> 'trace'),
                   SUM(effective_outcome = 'trace'),
                   SUM(state = 'uncertain'),
                   (SELECT COUNT(*) FROM action_attempts AS attempt
                    JOIN device_action_plans AS attempted
                      ON attempted.plan_id = attempt.plan_id
                    WHERE attempted.round_id = ?)
            FROM device_action_plans WHERE round_id = ?
            """,
            (blueprint.round_id, blueprint.round_id),
        ).fetchone()
        ai_states = dict(
            connection.execute(
                """
                SELECT state, COUNT(*) FROM browser_reply_plans
                WHERE account_id LIKE 'demo-account-%' AND plan_origin='ai'
                GROUP BY state
                """
            )
        )
        manual_handled = connection.execute(
            """
            SELECT COUNT(*) FROM browser_reply_plans
            WHERE account_id LIKE 'demo-account-%'
              AND plan_origin='manual' AND state='sent'
            """
        ).fetchone()[0]
        claimed_leases = connection.execute(
            """
            SELECT COUNT(*) FROM browser_action_leases
            WHERE account_id LIKE 'demo-account-%' AND state='claimed'
            """
        ).fetchone()[0]
        handled = connection.execute(
            """
            WITH handled(source_key) AS (
                SELECT source_inbound_fingerprint FROM browser_reply_plans
                WHERE account_id LIKE 'demo-account-%'
                  AND plan_origin='ai' AND state='sent'
                UNION ALL
                SELECT source_inbound_fingerprint FROM browser_reply_plans
                WHERE account_id LIKE 'demo-account-%'
                  AND plan_origin='manual' AND state='sent'
                UNION ALL
                SELECT message.message_id
                FROM web_messages AS message
                JOIN web_conversations AS conversation
                  ON conversation.account_id=message.account_id
                 AND conversation.conversation_id=message.conversation_id
                WHERE message.account_id LIKE 'demo-account-%'
                  AND message.direction='inbound'
                  AND conversation.human_required=1
            )
            SELECT COUNT(*), COUNT(DISTINCT source_key) FROM handled
            """
        ).fetchone()
        invalid_terminal_flags = connection.execute(
            """
            SELECT COUNT(*) FROM web_conversations
            WHERE account_id LIKE 'demo-account-%' AND (
                (human_required=1 AND stage <> 'human_required')
                OR (contact_captured_at_ms > 0
                    AND stage NOT IN ('contact_captured', 'closed'))
            )
            """
        ).fetchone()[0]

    assert tuple(counts) == (4_410, 68_420 - 4_410, 5, 4_410)
    account_ids = tuple(account.account_id for account in blueprint.accounts)
    funnel = Database(path).lead_funnel_snapshot(account_ids=account_ids)
    assert funnel == {
        "followers": 1_240,
        "dm_inbound": 486,
        "engaged": 326,
        "qualified": 173,
        "invited": 126,
        "contact_captured": 72,
        "human_required": 28,
    }
    assert Database(path).lead_sales_snapshot(account_ids=account_ids)["sales"] == 19
    assert ai_states == {"sent": 331, "superseded": 12, "uncertain": 5}
    assert manual_handled == 98
    assert 486 - (331 + manual_handled + 28) == 29
    assert 331 / (331 + manual_handled + 28) == pytest.approx(0.7243, abs=0.0001)
    assert tuple(handled) == (457, 457)
    assert invalid_terminal_flags == 0
    assert claimed_leases == 0
    assert Database(path).reply_latency_snapshot() == {
        "confirmed_replies": 331,
        "median_ms": 38_000,
        "p90_ms": 38_000,
    }
    assert result.summary["interaction_plans"] == 4_410
    assert result.summary["uncertain_action_plans"] == 5
    assert result.created_total == sum(result.created.values())


def test_seed_conversion_populates_inbox_settings_and_sales(tmp_path: Path) -> None:
    path = tmp_path / "tikpoc.db"
    accounts_path = tmp_path / "web-accounts.yaml"
    settings_path = tmp_path / "config/secrets/operator-settings.json"
    blueprint = build_demo_blueprint(
        now_ms=NOW_MS,
        scale=DemoScale.test_fixture(),
    )

    first = seed_demo_database(
        path,
        blueprint,
        web_accounts_path=accounts_path,
        runtime_settings_path=settings_path,
        backup_dir=tmp_path / "backups",
    )
    second = seed_demo_database(
        path,
        blueprint,
        web_accounts_path=accounts_path,
        runtime_settings_path=settings_path,
        backup_dir=tmp_path / "backups",
    )

    registry = WebAccountRegistry.from_path(accounts_path)
    database = Database(path)
    account_ids = tuple(item.account_id for item in registry.accounts)
    conversations = database.lead_conversations(
        account_ids=account_ids,
        limit=100,
        now_ms=blueprint.now_ms,
    )

    assert len(registry.accounts) == 3
    assert all(item.mode == "browser" for item in registry.accounts)
    assert all(item.enabled is False for item in registry.accounts)
    assert all(item.browser_dm_enabled is False for item in registry.accounts)
    assert all(item.browser_followback_enabled is False for item in registry.accounts)
    assert {item.expected_tiktok_username for item in registry.accounts} == {
        account.username for account in blueprint.accounts
    }
    assert {item.browser_profile_label for item in registry.accounts} == {
        account.profile_label for account in blueprint.accounts
    }
    assert {row["stage"] for row in conversations} >= {
        "qualified",
        "invited",
        "human_required",
        "closed",
    }
    assert database.lead_funnel_snapshot(account_ids=account_ids) == {
        "followers": 5,
        "dm_inbound": 8,
        "engaged": 7,
        "qualified": 6,
        "invited": 5,
        "contact_captured": 4,
        "human_required": 1,
    }
    assert database.lead_sales_snapshot(account_ids=account_ids)["sales"] == 3
    assert (
        RuntimeSettingsStore(settings_path).provider_credentials().key_configured
        is False
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "provider" not in settings or settings["provider"].get("api_key", "") == ""
    assert set(settings["accounts"]) == set(account_ids)

    with sqlite3.connect(path) as connection:
        message_counts = dict(
            connection.execute(
                """
                SELECT direction, COUNT(*) FROM web_messages
                WHERE account_id LIKE 'demo-account-%' GROUP BY direction
                """
            )
        )
        ai_states = dict(
            connection.execute(
                """
                SELECT state, COUNT(*) FROM browser_reply_plans
                WHERE account_id LIKE 'demo-account-%' AND plan_origin='ai'
                GROUP BY state
                """
            )
        )
        manual_outcomes = connection.execute(
            """
            SELECT COUNT(*) FROM browser_reply_plans
            WHERE account_id LIKE 'demo-account-%'
              AND plan_origin='manual' AND state='sent'
            """
        ).fetchone()[0]
        human_outcomes = connection.execute(
            """
            SELECT COUNT(*) FROM lead_funnel_events
            WHERE account_id LIKE 'demo-account-%' AND stage='human_required'
            """
        ).fetchone()[0]
        leases = connection.execute(
            """
            SELECT COUNT(*) FROM browser_action_leases
            WHERE account_id LIKE 'demo-account-%' AND state='claimed'
            """
        ).fetchone()[0]
        health = connection.execute(
            """
            SELECT page_role, COUNT(*) FROM browser_account_health
            WHERE account_id LIKE 'demo-account-%' GROUP BY page_role
            """
        ).fetchall()
        inconsistent_terminal_rows = connection.execute(
            """
            SELECT COUNT(*) FROM web_conversations
            WHERE account_id LIKE 'demo-account-%' AND (
                (stage='human_required' AND human_required <> 1)
                OR (human_required=1 AND stage <> 'human_required')
                OR (contact_captured_at_ms > 0
                    AND stage NOT IN ('contact_captured', 'closed'))
            )
            """
        ).fetchone()[0]

    assert message_counts == {"inbound": 8, "outbound": 8}
    assert ai_states == {"sent": 5, "uncertain": 1}
    assert manual_outcomes == 1
    assert human_outcomes == 1
    assert 5 / (5 + manual_outcomes + human_outcomes) == pytest.approx(5 / 7)
    assert leases == 0
    assert dict(health) == {"activity": 3, "messages": 3}
    assert inconsistent_terminal_rows == 0
    assert first.created["ai_reply_plans"] == 6
    assert first.created["manual_reply_plans"] == 1
    assert second.created_total == 0


def test_portfolio_conversion_contract_is_exact_and_detailed() -> None:
    blueprint = build_demo_blueprint(now_ms=NOW_MS)

    assert blueprint.metrics.inbound == 486
    assert blueprint.metrics.ai_plans == 348
    assert blueprint.metrics.ai_sent == 331
    assert blueprint.metrics.ai_uncertain == 5
    assert blueprint.metrics.ai_superseded == 12
    assert blueprint.scale.manual_handled == 98
    assert blueprint.scale.pending_inbound == 29
    assert 331 / (331 + 98 + 28) == pytest.approx(0.7243, abs=0.0001)
    assert len(blueprint.conversations) >= 20
    assert {item.language for item in blueprint.conversations} == {"zh", "en"}
    assert {item.stage for item in blueprint.conversations} >= {
        "engaged",
        "qualified",
        "invited",
        "contact_captured",
        "human_required",
        "closed",
    }
    combined_text = " ".join(
        f"{item.inbound_text} {item.outbound_text}" for item in blueprint.conversations
    )
    assert "demo@example.invalid" in combined_text
    assert "refund" in combined_text
    assert "取消" in combined_text
    assert "complaint" in combined_text


def test_clear_demo_database_preserves_unrelated_rows_and_settings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tikpoc.db"
    accounts_path = tmp_path / "web-accounts.yaml"
    settings_path = tmp_path / "operator-settings.json"
    blueprint = build_demo_blueprint(now_ms=NOW_MS, scale=DemoScale.test_fixture())
    database = Database(path)
    database.migrate()
    database.append_web_message(
        "real-account",
        "real-conversation",
        "real-message",
        direction="inbound",
        message_type="TEXT",
        text="keep me",
        timestamp_ms=NOW_MS,
        participant_username="real_customer",
    )
    database.record_lead_funnel_event(
        "real-account",
        "real_customer",
        "engaged",
        "real:source",
        conversation_id="real-conversation",
        occurred_at_ms=NOW_MS,
    )
    settings_path.write_text(
        json.dumps(
            {
                "provider": {
                    "base_url": "https://provider.example.invalid",
                    "api_key": "KEEP",
                    "model": "demo-model",
                },
                "accounts": {"real-account": {"brand_name": "Keep"}},
            }
        ),
        encoding="utf-8",
    )

    seed_demo_database(
        path,
        blueprint,
        web_accounts_path=accounts_path,
        runtime_settings_path=settings_path,
        backup_dir=tmp_path / "backups",
    )
    active_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert active_settings["provider"] == {
        "base_url": "https://provider.example.invalid",
        "api_key": "",
        "model": "demo-model",
    }
    result = clear_demo_database(
        path,
        web_accounts_path=accounts_path,
        runtime_settings_path=settings_path,
    )

    assert result.created_total > 0
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT text FROM web_messages WHERE account_id='real-account'"
            ).fetchone()[0]
            == "keep me"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM web_messages WHERE account_id LIKE 'demo-account-%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pool_targets WHERE identity_key LIKE 'demo:%'"
            ).fetchone()[0]
            == 0
        )
    restored_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored_settings == {
        "provider": {
            "base_url": "https://provider.example.invalid",
            "api_key": "",
            "model": "demo-model",
        },
        "accounts": {"real-account": {"brand_name": "Keep"}},
    }
    assert not accounts_path.exists()


def test_seed_uses_detailed_blueprint_identities_and_plan_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "portfolio.db"
    blueprint = build_demo_blueprint(now_ms=NOW_MS)
    seed_demo_database(path, blueprint)

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        for expected in blueprint.conversations:
            row = connection.execute(
                """
                SELECT account_id, conversation_id, participant_username, stage
                FROM web_conversations WHERE participant_username=?
                """,
                (expected.lead_id,),
            ).fetchone()
            assert row is not None
            assert dict(row) == {
                "account_id": expected.account_id,
                "conversation_id": expected.conversation_key,
                "participant_username": expected.lead_id,
                "stage": expected.stage,
            }
            inbound = connection.execute(
                """
                SELECT text FROM web_messages
                WHERE account_id=? AND conversation_id=? AND direction='inbound'
                """,
                (expected.account_id, expected.conversation_key),
            ).fetchone()
            assert inbound is not None and inbound[0] == expected.inbound_text

        plan_states = dict(
            connection.execute(
                """
                SELECT participant_username, state FROM browser_reply_plans
                WHERE participant_username IN ('demo_lead_002', 'demo_lead_003')
                  AND plan_origin='ai'
                """
            )
        )
        new_plan_count = connection.execute(
            """
            SELECT COUNT(*) FROM browser_reply_plans
            WHERE participant_username='demo_lead_010'
            """
        ).fetchone()[0]
        detailed_depths = dict(
            connection.execute(
                """
                SELECT conversation_id, COUNT(*) FROM web_messages
                WHERE conversation_id IN (?, ?)
                GROUP BY conversation_id
                """,
                (
                    blueprint.conversations[0].conversation_key,
                    blueprint.conversations[3].conversation_key,
                ),
            )
        )
        sent_examples = connection.execute(
            """
            SELECT COUNT(*) FROM browser_reply_plans AS plan
            WHERE plan.participant_username IN ('demo_lead_001', 'demo_lead_004')
              AND plan.plan_origin='ai' AND plan.state='sent'
              AND EXISTS (
                  SELECT 1 FROM web_messages AS message
                  WHERE message.account_id=plan.account_id
                    AND message.conversation_id=plan.conversation_id
                    AND message.direction='outbound'
                    AND message.in_reply_to_message_id=plan.inbound_fingerprint
              )
            """
        ).fetchone()[0]

    assert plan_states == {
        "demo_lead_002": "uncertain",
        "demo_lead_003": "superseded",
    }
    assert blueprint.conversations[9].stage == "new"
    assert new_plan_count == 0
    assert {
        blueprint.conversations[0].language,
        blueprint.conversations[3].language,
    } == {
        "zh",
        "en",
    }
    assert detailed_depths == {
        blueprint.conversations[0].conversation_key: 3,
        blueprint.conversations[3].conversation_key: 3,
    }
    assert sent_examples == 2


def test_configuration_promotion_failure_restores_database_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tikpoc.db"
    accounts_path = tmp_path / "web-accounts.yaml"
    settings_path = tmp_path / "operator-settings.json"
    accounts_before = (
        b"accounts:\n  - account_id: real-account\n    device_id: real-device\n"
    )
    settings_before = b'{"accounts":{"real-account":{"brand_name":"Keep"}}}'
    accounts_path.write_bytes(accounts_before)
    settings_path.write_bytes(settings_before)
    database = Database(path)
    database.migrate()
    database.append_web_message(
        "real-account",
        "real-conversation",
        "real-message",
        direction="inbound",
        message_type="TEXT",
        text="keep me",
        timestamp_ms=NOW_MS,
    )
    blueprint = build_demo_blueprint(now_ms=NOW_MS, scale=DemoScale.test_fixture())
    real_replace = demo_data.os.replace
    failed = False

    def fail_second_promotion(source: object, destination: object) -> None:
        nonlocal failed
        if Path(destination) == settings_path and not failed:
            failed = True
            raise OSError("settings promotion failed")
        real_replace(source, destination)

    monkeypatch.setattr(demo_data.os, "replace", fail_second_promotion)

    with pytest.raises(OSError, match="settings promotion failed"):
        seed_demo_database(
            path,
            blueprint,
            web_accounts_path=accounts_path,
            runtime_settings_path=settings_path,
            backup_dir=tmp_path / "backups",
        )

    assert accounts_path.read_bytes() == accounts_before
    assert settings_path.read_bytes() == settings_before
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT text FROM web_messages WHERE account_id='real-account'"
            ).fetchone()[0]
            == "keep me"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pool_targets WHERE identity_key LIKE 'demo:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM browser_reply_plans WHERE account_id LIKE 'demo-account-%'"
            ).fetchone()[0]
            == 0
        )
