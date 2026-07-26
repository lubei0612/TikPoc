from collections import Counter
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import ActionPlanState, ActionResult, OutcomeKind
from tikpoc.importer import Target
from tikpoc.models import ProfileMetrics
from tikpoc.outcome_planner import (
    HOURLY_LIMITS,
    draw_outcome,
    fixed_hour_start_ms,
    get_or_create_plan,
    plan_seed,
)
from tikpoc.rounds import create_exposure_round


def _eligible_repository(
    tmp_path: Path,
    *,
    target_count: int = 1,
    device_ids: tuple[str, ...] = ("phone-01",),
    eligible: bool = True,
    navigation_mode: str = "deeplink",
) -> tuple[AcquisitionRepository, str, tuple[str, ...]]:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    targets = tuple(
        Target(
            target_id=f"user-{index}",
            username=f"buyer_{index}",
            profile_url=f"https://www.tiktok.com/@buyer_{index}",
            source_video_id="video-1",
            sec_uid=f"sec-{index}",
            identity_key=f"sec:sec-{index}",
            source_line_numbers=(index + 2,),
        )
        for index in range(target_count)
    )
    pool = repository.import_pool("comments.csv", "e" * 64, targets)
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={
            device_id: f"seed-{index}"
            for index, device_id in enumerate(device_ids, start=1)
        },
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        navigation_mode=navigation_mode,
    )
    for device_index, device_id in enumerate(device_ids):
        while assignment := repository.claim_next_assignment(
            round_id,
            device_id,
            f"worker-{device_id}",
            now_ms=1_000 + device_index,
        ):
            repository.record_visit_confirmed(
                assignment.assignment_id,
                f"worker-{device_id}",
                now_ms=1_000 + device_index,
            )
            repository.release_assignment_lease(
                assignment.assignment_id, f"worker-{device_id}"
            )
    metrics = ProfileMetrics(20, 10, 5) if eligible else ProfileMetrics(10, 20, 0)
    for target in targets:
        assert repository.claim_snapshot_lease(
            round_id,
            target.identity_key,
            device_ids[0],
            now_ms=1_000,
            ttl_ms=30_000,
        )
        repository.publish_profile_snapshot(
            round_id,
            target.identity_key,
            device_id=device_ids[0],
            observed_username=target.username,
            metrics=metrics,
            private_account=False,
            observed_at_ms=1_001,
        )
    return repository, round_id, tuple(target.identity_key for target in targets)


def test_search_policy_with_posts_never_draws_trace(tmp_path: Path) -> None:
    repository, round_id, identities = _eligible_repository(
        tmp_path,
        target_count=12,
        eligible=True,
        navigation_mode="search",
    )

    plans = [
        get_or_create_plan(repository, round_id, identity_key, "phone-01", now_ms=1_000)
        for identity_key in identities
    ]

    assert {plan.requested_outcome for plan in plans} <= {
        OutcomeKind.LIKE,
        OutcomeKind.FAVORITE,
        OutcomeKind.REPOST,
    }
    assert {plan.policy_version for plan in plans} == {
        "search-posts-gte-1-composite-v1"
    }


def test_each_device_persists_an_independent_paced_plan(tmp_path: Path) -> None:
    repository, round_id, identities = _eligible_repository(
        tmp_path,
        device_ids=("phone-01", "phone-02", "phone-03"),
    )
    plans = [
        get_or_create_plan(
            repository,
            round_id,
            identities[0],
            device_id,
            now_ms=3_600_000,
        )
        for device_id in ("phone-01", "phone-02", "phone-03")
    ]

    assert len({plan.plan_id for plan in plans}) == 3
    assert all(plan.requested_outcome in set(OutcomeKind) for plan in plans)
    assert all(plan.requested_outcome is draw_outcome(plan.seed) for plan in plans)
    assert (
        get_or_create_plan(
            repository,
            round_id,
            identities[0],
            "phone-01",
            now_ms=3_600_100,
        )
        == plans[0]
    )


def test_dense_eligible_traffic_draws_all_outcomes_without_action_pacing(
    tmp_path: Path,
) -> None:
    repository, round_id, identities = _eligible_repository(tmp_path, target_count=202)
    started_at_ms = 3_600_000
    plans = [
        get_or_create_plan(
            repository,
            round_id,
            identity_key,
            "phone-01",
            now_ms=started_at_ms + round(index * 3_600_000 / (len(identities) - 1)),
        )
        for index, identity_key in enumerate(identities)
    ]
    requested = Counter(plan.requested_outcome for plan in plans)

    assert set(requested) == set(OutcomeKind)
    assert all(35 <= requested[outcome] <= 65 for outcome in OutcomeKind)
    assert all(plan.requested_outcome is draw_outcome(plan.seed) for plan in plans)
    assert any(
        plan.requested_outcome is not OutcomeKind.TRACE
        and plan.effective_outcome is OutcomeKind.TRACE
        for plan in plans
    )


@pytest.mark.parametrize(
    ("outcome", "limit"),
    [
        (OutcomeKind.LIKE, 100),
        (OutcomeKind.FAVORITE, 14),
        (OutcomeKind.REPOST, 25),
    ],
)
def test_full_selected_quota_becomes_trace_without_redraw(
    tmp_path: Path, outcome: OutcomeKind, limit: int
) -> None:
    repository, round_id, identities = _eligible_repository(
        tmp_path, target_count=limit + 1
    )

    for identity_key in identities[:limit]:
        plan = get_or_create_plan(
            repository,
            round_id,
            identity_key,
            "phone-01",
            now_ms=3_500_000,
            forced_draw=outcome,
        )
        assert plan.effective_outcome is outcome
    overflow = get_or_create_plan(
        repository,
        round_id,
        identities[-1],
        "phone-01",
        now_ms=3_500_000,
        forced_draw=outcome,
    )

    assert overflow.requested_outcome is outcome
    assert overflow.effective_outcome is OutcomeKind.TRACE
    assert overflow.quota_reason == f"{outcome.value}_limit_reached"
    quota = repository.quota_window("phone-01", outcome, window_start_ms=0)
    assert quota is not None and quota.reserved_count == limit


def test_fixed_hour_boundary_opens_a_new_quota_window(tmp_path: Path) -> None:
    repository, round_id, identities = _eligible_repository(tmp_path, target_count=15)
    for identity_key in identities[:14]:
        get_or_create_plan(
            repository,
            round_id,
            identity_key,
            "phone-01",
            now_ms=3_599_999,
            forced_draw=OutcomeKind.FAVORITE,
        )

    next_hour = get_or_create_plan(
        repository,
        round_id,
        identities[-1],
        "phone-01",
        now_ms=3_600_000,
        forced_draw=OutcomeKind.FAVORITE,
    )

    assert fixed_hour_start_ms(3_599_999) == 0
    assert fixed_hour_start_ms(3_600_000) == 3_600_000
    assert next_hour.effective_outcome is OutcomeKind.FAVORITE
    assert next_hour.quota_window_start_ms == 3_600_000


def test_ineligible_profile_forces_trace_without_quota(tmp_path: Path) -> None:
    repository, round_id, identities = _eligible_repository(tmp_path, eligible=False)

    plan = get_or_create_plan(
        repository,
        round_id,
        identities[0],
        "phone-01",
        now_ms=3_500_000,
        forced_draw=OutcomeKind.LIKE,
    )

    assert plan.requested_outcome is OutcomeKind.TRACE
    assert plan.effective_outcome is OutcomeKind.TRACE
    assert plan.quota_reason == "profile_ineligible"
    assert repository.quota_window("phone-01", OutcomeKind.LIKE, 0) is None


def test_visible_unavailable_action_becomes_confirmed_trace_and_releases_quota(
    tmp_path: Path,
) -> None:
    repository, round_id, identities = _eligible_repository(tmp_path)
    plan = get_or_create_plan(
        repository,
        round_id,
        identities[0],
        "phone-01",
        now_ms=3_500_000,
        forced_draw=OutcomeKind.REPOST,
    )
    with pytest.raises(ValueError, match="unavailable action evidence"):
        repository.confirm_action_unavailable_as_trace(plan.plan_id)
    repository.mark_action_executing(plan.plan_id)
    repository.record_action_result(
        plan.plan_id,
        ActionResult.UNAVAILABLE,
        now_ms=3_500_001,
    )

    fallback = repository.confirm_action_unavailable_as_trace(plan.plan_id)

    assert fallback.requested_outcome is OutcomeKind.REPOST
    assert fallback.effective_outcome is OutcomeKind.TRACE
    assert fallback.quota_reason == "repost_unavailable"
    assert fallback.quota_window_start_ms is None
    assert fallback.state is ActionPlanState.CONFIRMED
    quota = repository.quota_window("phone-01", OutcomeKind.REPOST, 0)
    assert quota is not None
    assert quota.reserved_count == 0
    assert quota.confirmed_count == 0
    assert quota.uncertain_count == 0


def test_unavailable_trace_fallback_rejects_non_repost_actions(tmp_path: Path) -> None:
    repository, round_id, identities = _eligible_repository(tmp_path)
    plan = get_or_create_plan(
        repository,
        round_id,
        identities[0],
        "phone-01",
        now_ms=3_500_000,
        forced_draw=OutcomeKind.LIKE,
    )
    repository.mark_action_executing(plan.plan_id)
    repository.record_action_result(
        plan.plan_id,
        ActionResult.UNAVAILABLE,
        now_ms=3_500_001,
    )

    with pytest.raises(ValueError, match="only supported for repost"):
        repository.confirm_action_unavailable_as_trace(plan.plan_id)

    unchanged = repository.action_plan(round_id, identities[0], "phone-01")
    quota = repository.quota_window("phone-01", OutcomeKind.LIKE, 0)
    assert unchanged is not None
    assert unchanged.effective_outcome is OutcomeKind.LIKE
    assert unchanged.state is ActionPlanState.PLANNED
    assert quota is not None and quota.reserved_count == 1


def test_seeded_draws_are_evenly_distributed() -> None:
    counts = Counter(
        draw_outcome(plan_seed("round-1", f"sec:user-{index}", "phone-01"))
        for index in range(4_000)
    )

    assert set(counts) == set(OutcomeKind)
    assert all(880 <= count <= 1_120 for count in counts.values())
    assert HOURLY_LIMITS == {
        OutcomeKind.LIKE: 100,
        OutcomeKind.FAVORITE: 14,
        OutcomeKind.REPOST: 25,
    }


def test_hourly_limits_are_immutable() -> None:
    with pytest.raises(TypeError):
        HOURLY_LIMITS[OutcomeKind.LIKE] = 99
