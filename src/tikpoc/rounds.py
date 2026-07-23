import hashlib
import json
from collections.abc import Mapping

from .acquisition_db import AcquisitionRepository


def device_order_key(round_id: str, device_seed: str, identity_key: str) -> str:
    payload = "\0".join((round_id, device_seed, identity_key)).encode()
    return hashlib.sha256(payload).hexdigest()


def create_exposure_round(
    repository: AcquisitionRepository,
    *,
    pool_id: str,
    device_seeds: Mapping[str, str],
    starts_at_ms: int,
    min_inter_device_gap_ms: int = 15 * 60 * 1000,
    min_repeat_gap_ms: int = 20 * 60 * 60 * 1000,
) -> str:
    normalized_items = [
        (str(device_id).strip(), str(seed).strip())
        for device_id, seed in device_seeds.items()
    ]
    normalized_device_ids = [device_id for device_id, _ in normalized_items]
    if len(set(normalized_device_ids)) != len(normalized_device_ids):
        raise ValueError("device ids must be unique after normalization")
    normalized_seeds = {
        str(device_id).strip(): str(seed).strip()
        for device_id, seed in normalized_items
    }
    if not normalized_seeds or any(
        not key or not value for key, value in normalized_seeds.items()
    ):
        raise ValueError("device ids and order seeds must be nonempty")
    if len(set(normalized_seeds.values())) != len(normalized_seeds):
        raise ValueError("device order seeds must be unique")
    if starts_at_ms < 0 or min_inter_device_gap_ms < 0 or min_repeat_gap_ms < 0:
        raise ValueError("round timestamps and gaps must be nonnegative")

    round_payload = json.dumps(
        {
            "pool_id": pool_id,
            "starts_at_ms": int(starts_at_ms),
            "device_seeds": sorted(normalized_seeds.items()),
        },
        separators=(",", ":"),
    )
    digest = hashlib.sha256(round_payload.encode()).hexdigest()
    round_id = f"round-{digest[:20]}"
    order_keys = {
        (target.identity_key, device_id): device_order_key(
            round_id, seed, target.identity_key
        )
        for target in repository.pool_targets(pool_id)
        for device_id, seed in normalized_seeds.items()
    }
    repository.create_round(
        round_id=round_id,
        pool_id=pool_id,
        device_seeds=normalized_seeds,
        starts_at_ms=int(starts_at_ms),
        min_inter_device_gap_ms=int(min_inter_device_gap_ms),
        min_repeat_gap_ms=int(min_repeat_gap_ms),
        order_keys=order_keys,
    )
    return round_id
