from collections.abc import Callable
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import ProfileAccessState
from tikpoc.importer import Target
from tikpoc.models import ProfileMetrics
from tikpoc.rounds import create_exposure_round


def _repository_with_round(
    tmp_path: Path,
    *,
    clock_ms: Callable[[], int] = lambda: 500,
    confirm_visits: bool = True,
) -> tuple[AcquisitionRepository, str, str]:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=clock_ms)
    repository.migrate()
    target = Target(
        target_id="user-1",
        username="buyer",
        profile_url="https://www.tiktok.com/@buyer",
        source_video_id="video-1",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )
    pool = repository.import_pool("comments.csv", "d" * 64, (target,))
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
    )
    if confirm_visits:
        for offset, device_id in enumerate(("phone-01", "phone-02")):
            owner_id = f"worker-{offset + 1}"
            assignment = repository.claim_next_assignment(
                round_id,
                device_id,
                owner_id,
                now_ms=1_000 + offset,
            )
            assert assignment is not None
            repository.record_visit_confirmed(
                assignment.assignment_id,
                owner_id,
                now_ms=1_000 + offset,
            )
            repository.release_assignment_lease(assignment.assignment_id, owner_id)
    return repository, round_id, target.identity_key


def test_only_one_device_owns_snapshot_lease(tmp_path: Path) -> None:
    repository, round_id, identity_key = _repository_with_round(tmp_path)

    first = repository.claim_snapshot_lease(
        round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000
    )
    second = repository.claim_snapshot_lease(
        round_id, identity_key, "phone-02", now_ms=1_001, ttl_ms=30_000
    )

    assert first is True
    assert second is False


def test_snapshot_lease_requires_a_confirmed_device_visit(tmp_path: Path) -> None:
    repository, round_id, identity_key = _repository_with_round(
        tmp_path, confirm_visits=False
    )

    with pytest.raises(ValueError, match="confirmed profile visit"):
        repository.claim_snapshot_lease(
            round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000
        )


def test_completed_snapshot_is_shared_by_every_device(tmp_path: Path) -> None:
    repository, round_id, identity_key = _repository_with_round(tmp_path)
    repository.claim_snapshot_lease(
        round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000
    )

    published = repository.publish_profile_snapshot(
        round_id,
        identity_key,
        device_id="phone-01",
        observed_username="buyer",
        metrics=ProfileMetrics(following=20, followers=10, posts=5),
        private_account=False,
        observed_at_ms=2_000,
    )

    assert published.eligible is True
    assert published.reason == "eligible"
    assert repository.profile_snapshot(round_id, identity_key) == published
    assert (
        repository.claim_snapshot_lease(
            round_id, identity_key, "phone-02", now_ms=2_001, ttl_ms=30_000
        )
        is False
    )


def test_expired_snapshot_lease_can_be_taken_over(tmp_path: Path) -> None:
    repository, round_id, identity_key = _repository_with_round(tmp_path)
    assert repository.claim_snapshot_lease(
        round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=100
    )
    assert not repository.claim_snapshot_lease(
        round_id, identity_key, "phone-02", now_ms=1_099, ttl_ms=100
    )
    assert repository.claim_snapshot_lease(
        round_id, identity_key, "phone-02", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(ValueError, match="snapshot lease"):
        repository.publish_profile_snapshot(
            round_id,
            identity_key,
            device_id="phone-01",
            observed_username="buyer",
            metrics=ProfileMetrics(20, 10, 5),
            private_account=False,
            observed_at_ms=1_101,
        )


def test_snapshot_is_scoped_to_one_round(tmp_path: Path) -> None:
    repository, round_id, identity_key = _repository_with_round(tmp_path)
    repository.claim_snapshot_lease(
        round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000
    )
    repository.publish_profile_snapshot(
        round_id,
        identity_key,
        device_id="phone-01",
        observed_username="buyer",
        metrics=ProfileMetrics(20, 10, 5),
        private_account=False,
        observed_at_ms=2_000,
    )

    assert repository.profile_snapshot("round-other", identity_key) is None


def test_private_access_state_normalizes_private_account_flag(tmp_path: Path) -> None:
    repository, round_id, identity_key = _repository_with_round(tmp_path)
    repository.claim_snapshot_lease(
        round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000
    )

    snapshot = repository.publish_profile_snapshot(
        round_id,
        identity_key,
        device_id="phone-01",
        observed_username="buyer",
        metrics=None,
        private_account=False,
        access_state=ProfileAccessState.PRIVATE,
        observed_at_ms=2_000,
    )

    assert snapshot.private_account is True
    assert snapshot.access_state is ProfileAccessState.PRIVATE
    assert snapshot.eligible is False


def test_snapshot_publication_rejects_a_lease_expired_after_observation(
    tmp_path: Path,
) -> None:
    current_ms = [1_000]
    repository, round_id, identity_key = _repository_with_round(
        tmp_path, clock_ms=lambda: current_ms[0]
    )
    repository.claim_snapshot_lease(
        round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=100
    )
    current_ms[0] = 1_200

    with pytest.raises(ValueError, match="snapshot lease"):
        repository.publish_profile_snapshot(
            round_id,
            identity_key,
            device_id="phone-01",
            observed_username="buyer",
            metrics=ProfileMetrics(20, 10, 5),
            private_account=False,
            observed_at_ms=1_050,
        )
