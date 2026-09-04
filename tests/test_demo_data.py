from dataclasses import FrozenInstanceError

import pytest

from tikpoc.demo_data import DemoScale, build_demo_blueprint

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
