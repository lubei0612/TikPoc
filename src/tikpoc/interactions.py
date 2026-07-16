import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .db import Database


@dataclass(frozen=True)
class ActionPolicy:
    enabled: bool = False
    probability: float = 0.0
    hourly_limit: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")
        if self.hourly_limit < 0:
            raise ValueError("hourly limit must be nonnegative")


@dataclass(frozen=True)
class InteractionPolicy:
    like: ActionPolicy = field(default_factory=ActionPolicy)
    favorite: ActionPolicy = field(default_factory=ActionPolicy)
    share: ActionPolicy = field(default_factory=ActionPolicy)
    trace_probability: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.trace_probability <= 1.0:
            raise ValueError("trace probability must be between 0 and 1")


def plan_actions(
    database: Database,
    policy: InteractionPolicy,
    *,
    random_seed: int | None = None,
    now: datetime | None = None,
    device_id: str = "default",
) -> tuple[str, ...]:
    rng = random.Random(random_seed)
    current = now or datetime.now(timezone.utc)
    window = current.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).isoformat()
    choices = [
        (action, getattr(policy, action).probability)
        for action in ("like", "favorite", "share")
        if getattr(policy, action).enabled
    ]
    choices.append(("trace", policy.trace_probability))
    total_weight = sum(weight for _, weight in choices)
    if total_weight <= 0:
        return ()
    draw = rng.random() * total_weight
    cumulative = 0.0
    selected = "trace"
    for action, weight in choices:
        cumulative += weight
        if draw < cumulative:
            selected = action
            break
    if selected == "trace":
        return ()
    action_policy = getattr(policy, selected)
    if database.reserve_action(
        selected, window, action_policy.hourly_limit, device_id
    ):
        return (selected,)
    return ()
