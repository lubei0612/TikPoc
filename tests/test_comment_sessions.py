from __future__ import annotations

from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.comment_sessions import CommentSessionService
from tikpoc.hot_comment_planner import CommentCandidate, CommentEvidence

NOW_MS = 1_774_905_600_000


def service(tmp_path: Path, *, limit: int = 20) -> CommentSessionService:
    repository = AcquisitionRepository(
        tmp_path / "acquisition.db", clock_ms=lambda: NOW_MS
    )
    repository.migrate()
    return CommentSessionService(repository, clock_ms=lambda: NOW_MS, daily_limit=limit)


def candidate(persona: str = "zoey", suffix: str = "") -> CommentCandidate:
    return CommentCandidate(
        f"The shape makes this styling feel so polished{suffix} ✨",
        f"这个包型让整套搭配显得很精致{suffix} ✨",
        1,
        persona,
    )


def test_migration_creates_comment_domain_tables(tmp_path: Path) -> None:
    sessions = service(tmp_path)
    assert set(sessions.table_names()) >= {
        "comment_videos",
        "comment_evidence",
        "comment_personas",
        "comment_plans",
        "comment_attempts",
        "comment_observations",
    }


def test_evidence_is_deduplicated_globally_by_cid(tmp_path: Path) -> None:
    sessions = service(tmp_path)
    first = sessions.add_video("https://www.tiktok.com/@a/video/7523456789012345678")
    second = sessions.add_video("https://www.tiktok.com/@b/video/7523456789012345679")
    item = CommentEvidence("cid-1", "Beautiful styling", 12, 2, 100, "en")

    assert sessions.import_evidence(first.video_id, [item]) == 1
    assert sessions.import_evidence(first.video_id, [item]) == 0
    assert sessions.import_evidence(second.video_id, [item]) == 0


def test_approved_plan_is_immutable_and_unique_per_account_video(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())

    plan = sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    replacement = sessions.save_candidate(video.video_id, candidate(suffix=" today"))

    assert sessions.plan(plan.plan_id).english == candidate().english
    with pytest.raises(ValueError, match="approved_plan_exists"):
        sessions.approve_plan("account-1", video.video_id, replacement.candidate_id)


def test_plan_persona_must_belong_to_approving_account(tmp_path: Path) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())

    with pytest.raises(ValueError, match="persona_account_mismatch"):
        sessions.approve_plan("account-2", video.video_id, draft.candidate_id)


def test_candidate_command_id_replays_without_creating_a_second_draft(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")

    first = sessions.save_candidate(video.video_id, candidate(), command_id="draft-1")
    replay = sessions.save_candidate(video.video_id, candidate(), command_id="draft-1")

    assert replay == first


def test_claim_is_account_scoped_and_unique(tmp_path: Path) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())
    sessions.approve_plan("account-1", video.video_id, draft.candidate_id)

    first = sessions.claim_for_account("account-1", "worker-1")
    second = sessions.claim_for_account("account-1", "worker-2")

    assert first is not None
    assert second is None


def test_local_day_quota_counts_confirmed_and_unresolved_submissions(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path, limit=2)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    for offset in range(3):
        video = sessions.add_video(str(7523456789012345678 + offset))
        draft = sessions.save_candidate(video.video_id, candidate(suffix=str(offset)))
        sessions.approve_plan("account-1", video.video_id, draft.candidate_id)

    first = sessions.claim_for_account("account-1", "worker")
    assert first is not None
    sessions.record_submission(first.plan_id, "submit-1", state="visible_confirmed")
    second = sessions.claim_for_account("account-1", "worker")
    assert second is not None
    sessions.record_submission(second.plan_id, "submit-2", state="uncertain")

    assert sessions.claim_for_account("account-1", "worker") is None


def test_reconciliation_and_observation_are_idempotently_recorded(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())
    plan = sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    sessions.record_submission(plan.plan_id, "submit-1", state="uncertain")

    sessions.record_reconciliation(plan.plan_id, "submit-1", visible=True)
    sessions.record_reconciliation(plan.plan_id, "submit-1", visible=True)
    sessions.record_observation(plan.plan_id, likes=3, replies=1)

    assert sessions.attempt_count(plan.plan_id) == 1
    assert sessions.observation_count(plan.plan_id) == 1


def test_visible_confirmation_does_not_regress_to_uncertain(tmp_path: Path) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())
    plan = sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    claimed = sessions.claim_for_account("account-1", "worker")
    assert claimed is not None
    sessions.record_submission(plan.plan_id, "submit-1", state="visible_confirmed")

    sessions.record_reconciliation(plan.plan_id, "submit-1", visible=False)

    assert sessions.plan(plan.plan_id).state == "visible_confirmed"


def test_submission_idempotency_key_cannot_move_to_another_plan(tmp_path: Path) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    plans = []
    for offset in range(2):
        video = sessions.add_video(str(7523456789012345678 + offset))
        draft = sessions.save_candidate(video.video_id, candidate(suffix=str(offset)))
        plans.append(
            sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
        )
    sessions.claim_for_account("account-1", "worker")
    sessions.record_submission(plans[0].plan_id, "submit-1", state="uncertain")

    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        sessions.record_submission(plans[1].plan_id, "submit-1", state="uncertain")

    assert sessions.plan(plans[1].plan_id).state == "approved"


def test_uncertain_plan_is_returned_only_for_read_only_reconciliation(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())
    plan = sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    sessions.claim_for_account("account-1", "worker")
    sessions.record_submission(plan.plan_id, "submit-1", state="uncertain")

    continuation = sessions.claim_for_account(
        "account-1", "worker", include_reconciliation=True
    )

    assert continuation is not None
    assert continuation.plan_id == plan.plan_id
    assert continuation.state == "uncertain"


def test_verification_recovery_requires_ack_and_stable_home_per_device(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path)
    sessions.record_verification_required(
        "device-1", "account-1", 42, phase="comment_submitting"
    )

    assert sessions.complete_stable_home("device-1", "account-1") is False
    acknowledged = sessions.acknowledge_recovery("device-1", command_id="recover-1")
    replay = sessions.acknowledge_recovery("device-1", command_id="recover-1")

    assert acknowledged == replay
    assert sessions.device_block("device-1")["state"] == "recovery_requested"
    assert sessions.device_block("device-2") is None
    assert sessions.complete_stable_home("device-1", "account-1") is True
    assert sessions.device_block("device-1") is None


def test_metrics_retain_unresolved_quota_and_verification_counts(
    tmp_path: Path,
) -> None:
    sessions = service(tmp_path)
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video("7523456789012345678")
    draft = sessions.save_candidate(video.video_id, candidate())
    plan = sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    sessions.claim_for_account("account-1", "worker")
    sessions.record_submission(plan.plan_id, "submit-1", state="uncertain")
    sessions.record_observation(plan.plan_id, likes=3, replies=1)
    sessions.record_verification_required(
        "device-1",
        "account-1",
        plan.plan_id,
        phase="comment_reconciling",
        event_key="verify-1",
    )
    sessions.record_verification_required(
        "device-1",
        "account-1",
        plan.plan_id,
        phase="comment_reconciling",
        event_key="verify-1",
    )

    metrics = sessions.metrics("account-1")

    assert metrics["planned"] == 1
    assert metrics["submitted"] == 1
    assert metrics["uncertain"] == 1
    assert metrics["verification_required"] == 1
    assert metrics["observed_likes"] == 3
    assert metrics["observed_replies"] == 1
