import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import fmean
from typing import Iterable, Protocol


MEAN_LIMIT_MS = 6_500
P90_LIMIT_MS = 8_640


@dataclass(frozen=True)
class AssignmentTiming:
    assignment_id: int
    identity_key: str
    device_id: str
    duration_ms: int


@dataclass(frozen=True)
class DeviceCapacity:
    confirmed: int
    mean_ms: float
    p90_ms: float
    confirmed_per_hour: float
    projected_per_effective_day: int
    passed: bool


@dataclass(frozen=True)
class CapacityReport:
    measured_seconds: float
    slowest_device_id: str
    projected_unique_per_day: int
    fully_covered_targets: int
    uncertain_count: int
    devices: dict[str, DeviceCapacity]
    reasons: tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class RoundCapacityAudit:
    device_ids: tuple[str, ...]
    timings: tuple[AssignmentTiming, ...]
    total_assignment_count: int
    fully_covered_targets: int
    uncertain_count: int
    identity_mismatch_count: int
    false_success_count: int
    quota_overrun_count: int
    deferred_count: int


class CapacityAuditRepository(Protocol):
    def capacity_audit(
        self, round_id: str, *, expected_devices: int
    ) -> RoundCapacityAudit: ...


def _nearest_rank_p90(values: list[int]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.9) - 1)
    return float(ordered[index])


def evaluate_capacity(
    rows: Iterable[AssignmentTiming],
    *,
    expected_devices: int,
    expected_device_ids: Iterable[str] | None = None,
    target_count: int,
    effective_hours: float,
    uncertain_count: int = 0,
    fully_covered_targets: int | None = None,
    total_assignment_count: int | None = None,
    identity_mismatch_count: int = 0,
    false_success_count: int = 0,
    quota_overrun_count: int = 0,
    deferred_count: int = 0,
) -> CapacityReport:
    completed = tuple(rows)
    if expected_devices <= 0:
        raise ValueError("expected device count must be positive")
    if target_count <= 0:
        raise ValueError("target count must be positive")
    if effective_hours <= 0 or not math.isfinite(effective_hours):
        raise ValueError("effective hours must be positive and finite")
    audit_counts = (
        uncertain_count,
        identity_mismatch_count,
        false_success_count,
        quota_overrun_count,
        deferred_count,
    )
    if any(value < 0 for value in audit_counts):
        raise ValueError("audit counts must be nonnegative")
    if fully_covered_targets is not None and not (
        0 <= fully_covered_targets <= target_count
    ):
        raise ValueError("fully covered targets must be within target count")
    if total_assignment_count is not None and total_assignment_count < 0:
        raise ValueError("total assignment count must be nonnegative")
    if any(
        row.assignment_id <= 0
        or not row.identity_key.strip()
        or not row.device_id.strip()
        or row.duration_ms <= 0
        for row in completed
    ):
        raise ValueError("completed assignment timing is invalid")
    if len({row.assignment_id for row in completed}) != len(completed):
        raise ValueError("completed assignment timing is duplicated")

    durations_by_device: dict[str, list[int]] = defaultdict(list)
    devices_by_target: dict[str, list[str]] = defaultdict(list)
    for row in completed:
        durations_by_device[row.device_id].append(row.duration_ms)
        devices_by_target[row.identity_key].append(row.device_id)

    configured_device_ids = (
        tuple(sorted(durations_by_device))
        if expected_device_ids is None
        else tuple(sorted(str(value).strip() for value in expected_device_ids))
    )
    all_device_ids = sorted(set(configured_device_ids) | set(durations_by_device))
    devices: dict[str, DeviceCapacity] = {}
    measured_seconds = 0.0
    for device_id in all_device_ids:
        durations = durations_by_device.get(device_id, [])
        if not durations:
            devices[device_id] = DeviceCapacity(
                confirmed=0,
                mean_ms=0.0,
                p90_ms=0.0,
                confirmed_per_hour=0.0,
                projected_per_effective_day=0,
                passed=False,
            )
            continue
        mean_ms = fmean(durations)
        p90_ms = _nearest_rank_p90(durations)
        confirmed_per_hour = 3_600_000 / mean_ms
        projected = math.floor(confirmed_per_hour * effective_hours)
        measured_seconds = max(measured_seconds, sum(durations) / 1_000)
        devices[device_id] = DeviceCapacity(
            confirmed=len(durations),
            mean_ms=mean_ms,
            p90_ms=p90_ms,
            confirmed_per_hour=confirmed_per_hour,
            projected_per_effective_day=projected,
            passed=mean_ms < MEAN_LIMIT_MS and p90_ms < P90_LIMIT_MS,
        )

    if fully_covered_targets is None:
        fully_covered_targets = sum(
            len(device_ids) == expected_devices
            and len(set(device_ids)) == expected_devices
            for device_ids in devices_by_target.values()
        )

    reasons: list[str] = []
    if uncertain_count:
        reasons.append("uncertain assignments")
    if identity_mismatch_count:
        reasons.append("identity mismatches")
    if false_success_count:
        reasons.append("false completed outcomes")
    if quota_overrun_count:
        reasons.append("quota overruns")
    if deferred_count:
        reasons.append("pending deferred work")
    if fully_covered_targets != target_count:
        reasons.append(f"{expected_devices}/{expected_devices} coverage incomplete")
    observed_device_ids = set(durations_by_device)
    if (
        len(configured_device_ids) != expected_devices
        or len(set(configured_device_ids)) != expected_devices
        or observed_device_ids != set(configured_device_ids)
    ):
        reasons.append("expected device count incomplete")
    expected_assignment_count = expected_devices * target_count
    observed_assignment_count = (
        len(completed) if total_assignment_count is None else total_assignment_count
    )
    if (
        observed_assignment_count != expected_assignment_count
        or len(completed) != expected_assignment_count
    ):
        reasons.append("assignment cardinality mismatch")
    if any(not device.passed for device in devices.values()):
        reasons.append("device timing threshold exceeded")

    slowest_device_id = ""
    projected_unique_per_day = 0
    if devices:
        slowest_device_id = min(
            devices,
            key=lambda device_id: (
                devices[device_id].projected_per_effective_day,
                device_id,
            ),
        )
        projected_unique_per_day = devices[
            slowest_device_id
        ].projected_per_effective_day
    if projected_unique_per_day < target_count:
        reasons.append("projected capacity below target")

    return CapacityReport(
        measured_seconds=measured_seconds,
        slowest_device_id=slowest_device_id,
        projected_unique_per_day=projected_unique_per_day,
        fully_covered_targets=fully_covered_targets,
        uncertain_count=uncertain_count,
        devices=devices,
        reasons=tuple(reasons),
        passed=not reasons,
    )


def evaluate_round_capacity(
    repository: CapacityAuditRepository,
    round_id: str,
    *,
    expected_devices: int,
    target_count: int,
    effective_hours: float,
) -> CapacityReport:
    audit = repository.capacity_audit(round_id, expected_devices=expected_devices)
    return evaluate_capacity(
        audit.timings,
        expected_devices=expected_devices,
        expected_device_ids=audit.device_ids,
        target_count=target_count,
        effective_hours=effective_hours,
        uncertain_count=audit.uncertain_count,
        fully_covered_targets=audit.fully_covered_targets,
        total_assignment_count=audit.total_assignment_count,
        identity_mismatch_count=audit.identity_mismatch_count,
        false_success_count=audit.false_success_count,
        quota_overrun_count=audit.quota_overrun_count,
        deferred_count=audit.deferred_count,
    )
