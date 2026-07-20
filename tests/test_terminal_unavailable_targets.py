from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import (
    AssignmentPhase,
    DeviceDiagnostics,
    ProfileAccessState,
)
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def _target() -> Target:
    return Target(
        target_id="user-s1",
        username="buyer_s1",
        profile_url="https://www.tiktok.com/@buyer_s1",
        source_video_id="video-1",
        sec_uid="s1",
        identity_key="sec:s1",
        source_line_numbers=(2,),
    )


def _round(repository: AcquisitionRepository) -> str:
    imported = repository.import_pool("comments.csv", "f" * 64, (_target(),))
    return create_exposure_round(
        repository,
        pool_id=imported.pool_id,
        device_seeds={"phone-01": "seed-01", "phone-02": "seed-02"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )


def test_permanently_unavailable_assignment_skips_after_first_attempt(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    round_id = _round(repository)
    claimed = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert claimed is not None and claimed.attempt_count == 1

    state, skipped = repository.record_permanently_unavailable(
        claimed.assignment_id,
        "worker-01",
        observed_username="buyer_s1",
        observed_by_device_id="phone-01",
        now_ms=1_100,
        diagnostics=DeviceDiagnostics(ui_summary="explicit unavailable marker"),
    )

    assert state.access_state is ProfileAccessState.PERMANENTLY_UNAVAILABLE
    assert state.reason == "permanently_unavailable"
    assert skipped.phase is AssignmentPhase.SKIPPED
    assert skipped.attempt_count == 1
    assert skipped.visit_confirmed_at_ms is None
    assert skipped.last_error_code == "profile_permanently_unavailable"
    assert repository.round_coverage(round_id)["confirmed_visits"] == 0
    transition = repository.assignment_phase_history(claimed.assignment_id)[-1]
    assert transition.details["attempt_count"] == 1
    assert transition.details["error_code"] == "profile_permanently_unavailable"


def test_marked_target_skips_sibling_device_without_three_attempts(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    round_id = _round(repository)
    first = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert first is not None
    repository.record_permanently_unavailable(
        first.assignment_id,
        "worker-01",
        observed_username="buyer_s1",
        observed_by_device_id="phone-01",
        now_ms=1_100,
        diagnostics=DeviceDiagnostics(),
    )
    sibling = repository.claim_next_assignment(
        round_id, "phone-02", "worker-02", now_ms=1_101
    )
    assert sibling is not None and sibling.attempt_count == 1

    skipped = repository.skip_marked_permanently_unavailable(
        sibling.assignment_id, "worker-02", now_ms=1_102
    )

    assert skipped.phase is AssignmentPhase.SKIPPED
    assert skipped.attempt_count == 1
    assert skipped.visit_confirmed_at_ms is None


def test_confirmed_visit_cannot_be_reclassified_as_unavailable(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    round_id = _round(repository)
    claimed = repository.claim_next_assignment(
        round_id, "phone-01", "worker-01", now_ms=1_000
    )
    assert claimed is not None
    repository.record_visit_confirmed(claimed.assignment_id, "worker-01", now_ms=1_050)

    with pytest.raises(ValueError, match="confirmed visit"):
        repository.record_permanently_unavailable(
            claimed.assignment_id,
            "worker-01",
            observed_username="buyer_s1",
            observed_by_device_id="phone-01",
            now_ms=1_100,
            diagnostics=DeviceDiagnostics(),
        )
