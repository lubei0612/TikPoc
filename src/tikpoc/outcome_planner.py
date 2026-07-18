import hashlib
import random
from types import MappingProxyType

from .acquisition_db import AcquisitionRepository
from .acquisition_models import ActionPlan, OutcomeKind


OUTCOMES = (
    OutcomeKind.LIKE,
    OutcomeKind.FAVORITE,
    OutcomeKind.REPOST,
    OutcomeKind.TRACE,
)
HOURLY_LIMITS = MappingProxyType(
    {
        OutcomeKind.LIKE: 100,
        OutcomeKind.FAVORITE: 14,
        OutcomeKind.REPOST: 25,
    }
)


def plan_seed(round_id: str, identity_key: str, device_id: str) -> str:
    payload = "\0".join((round_id, identity_key, device_id)).encode()
    return hashlib.sha256(payload).hexdigest()


def draw_outcome(seed: str) -> OutcomeKind:
    return OUTCOMES[random.Random(seed).randrange(len(OUTCOMES))]


def fixed_hour_start_ms(now_ms: int) -> int:
    if now_ms < 0:
        raise ValueError("quota timestamp must be nonnegative")
    return now_ms - now_ms % 3_600_000


def get_or_create_plan(
    repository: AcquisitionRepository,
    round_id: str,
    identity_key: str,
    device_id: str,
    *,
    now_ms: int,
    forced_draw: OutcomeKind | str | None = None,
) -> ActionPlan:
    existing = repository.action_plan(round_id, identity_key, device_id)
    if existing is not None:
        return existing
    seed = plan_seed(round_id, identity_key, device_id)
    requested_outcome = (
        draw_outcome(seed) if forced_draw is None else OutcomeKind(forced_draw)
    )
    if forced_draw is None:
        return repository.create_paced_action_plan(
            round_id=round_id,
            identity_key=identity_key,
            device_id=device_id,
            seed=seed,
            now_ms=now_ms,
            hourly_limits=HOURLY_LIMITS,
        )
    return repository.create_action_plan(
        round_id=round_id,
        identity_key=identity_key,
        device_id=device_id,
        seed=seed,
        requested_outcome=requested_outcome,
        now_ms=now_ms,
        hourly_limits=HOURLY_LIMITS,
    )
