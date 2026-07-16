from datetime import datetime, timezone
from pathlib import Path

import pytest

from tikpoc.db import Database
from tikpoc.interactions import ActionPolicy, InteractionPolicy, plan_actions


def test_action_policy_rejects_invalid_probability() -> None:
    with pytest.raises(ValueError, match="probability must be between 0 and 1"):
        ActionPolicy(enabled=True, probability=1.1, hourly_limit=2)


def test_plan_actions_selects_exactly_one_equal_weight_outcome(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    policy = InteractionPolicy(
        like=ActionPolicy(True, 0.25, 100),
        favorite=ActionPolicy(True, 0.25, 14),
        share=ActionPolicy(True, 0.25, 25),
        trace_probability=0.25,
    )
    now = datetime(2026, 7, 11, 10, 15, tzinfo=timezone.utc)

    outcomes = {
        plan_actions(database, policy, random_seed=seed, now=now)
        for seed in range(100)
    }

    assert outcomes == {(), ("like",), ("favorite",), ("share",)}


def test_selected_action_degrades_to_trace_when_hourly_quota_is_full(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    policy = InteractionPolicy(
        like=ActionPolicy(True, 1.0, 1),
        trace_probability=0.0,
    )
    now = datetime(2026, 7, 11, 10, 15, tzinfo=timezone.utc)

    assert plan_actions(database, policy, random_seed=1, now=now) == ("like",)
    assert plan_actions(database, policy, random_seed=1, now=now) == ()


def test_quota_rolls_over_at_next_utc_hour(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    policy = InteractionPolicy(
        like=ActionPolicy(True, 1.0, 1), trace_probability=0.0
    )

    first = datetime(2026, 7, 11, 10, 59, tzinfo=timezone.utc)
    second = datetime(2026, 7, 11, 11, 0, tzinfo=timezone.utc)

    assert plan_actions(database, policy, random_seed=1, now=first) == ("like",)
    assert plan_actions(database, policy, random_seed=1, now=first) == ()
    assert plan_actions(database, policy, random_seed=1, now=second) == ("like",)
