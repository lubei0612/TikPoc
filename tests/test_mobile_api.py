import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import ActionPlanState, AssignmentPhase, OutcomeKind
from tikpoc.api import create_app
from tikpoc.hot_comment_planner import CommentCandidate
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round


def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            tmp_path / "mobile.db",
            clock=lambda: 12.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )


def test_api_wires_configured_comment_interval(tmp_path: Path) -> None:
    now_s = [12.0]
    api = TestClient(
        create_app(
            tmp_path / "paced.db",
            clock=lambda: now_s[0],
            mobile_bootstrap_token="bootstrap-secret",
            comment_submission_interval_ms=20 * 60_000,
            comment_submission_jitter_ms=0,
        )
    )
    sessions = api.app.state.comment_sessions
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    for offset in range(2):
        video = sessions.add_video(str(7523456789012345678 + offset))
        draft = sessions.save_candidate(
            video.video_id,
            CommentCandidate(
                f"A polished shape that changes the whole outfit {offset}",
                f"这个精致包型改变了整套穿搭 {offset}",
                0,
                "zoey",
            ),
        )
        sessions.approve_plan("account-1", video.video_id, draft.candidate_id)

    first = sessions.claim_for_account("account-1", "worker")
    assert first is not None
    sessions.record_submission(first.plan_id, "submit-1", state="visible_confirmed")
    now_s[0] += 20 * 60 - 0.001
    assert sessions.claim_for_account("account-1", "worker") is None
    now_s[0] += 0.001
    assert sessions.claim_for_account("account-1", "worker") is not None


def test_mobile_registration_requires_bootstrap_bearer(tmp_path: Path) -> None:
    api = client(tmp_path)
    payload = {"device_id": "device-1", "account_id": "account-1"}

    missing = api.post("/api/mobile/register", json=payload)
    registered = api.post(
        "/api/mobile/register",
        json=payload,
        headers={"Authorization": "Bearer bootstrap-secret"},
    )

    assert missing.status_code == 401
    assert registered.status_code == 200
    assert registered.json() == {
        "device_id": "device-1",
        "account_id": "account-1",
        "session_epoch": 1,
        "access_token": registered.json()["access_token"],
    }
    assert registered.json()["access_token"]


def test_mobile_claims_immutable_brand_comment_and_verification_preserves_it(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    sessions = api.app.state.comment_sessions
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video(
        "https://www.tiktok.com/@bag/video/7523456789012345678",
        creator_username="bag",
        caption_anchor="rare archive piece",
    )
    draft = sessions.save_candidate(
        video.video_id,
        CommentCandidate(
            "That structured shape changes the whole outfit ✨",
            "这个有型的包型改变了整套穿搭 ✨",
            1,
            "zoey",
        ),
    )
    sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}

    pulled = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_kind": "brand_comment",
            "limit": 1,
        },
        headers=headers,
    )
    task = pulled.json()["tasks"][0]
    assert task["task_kind"] == "brand_comment"
    assert task["video_id"] == video.video_id
    assert task["video_url"].startswith("https://www.tiktok.com/")
    assert task["creator_username"] == "bag"
    assert task["caption_anchor"] == "rare archive piece"
    assert task["publish_text"].startswith("That structured")
    assert task["lease_expires_at_ms"] == 132_000
    blocked = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "verify-1",
            "state": "deferred",
            "phase": "comment_submitting",
            "evidence": {"error_code": "verification_required"},
        },
        headers=headers,
    )
    assert blocked.json() == {
        "accepted": True,
        "state": "accepted",
        "comment_state": "verification_required",
    }
    replay_pull = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_kind": "brand_comment",
            "limit": 1,
        },
        headers=headers,
    )
    assert replay_pull.json()["tasks"] == []
    stable_home = {
        "device_id": "device-1",
        "session_epoch": 1,
        "app_version": "1.0.0",
        "phase": "stable_home",
        "queue_depth": 1,
        "client_timestamp_ms": 12_000,
    }
    assert (
        api.post("/api/mobile/heartbeat", json=stable_home, headers=headers).status_code
        == 409
    )
    api.post(
        "/api/comment-recovery/device-1/acknowledge",
        json={"command_id": "recover-1"},
    )
    assert (
        api.post("/api/mobile/heartbeat", json=stable_home, headers=headers).status_code
        == 200
    )
    completed = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "comment-result-1",
            "state": "completed",
            "phase": "comment_reconciling",
            "evidence": {"visible_confirmed": True},
        },
        headers=headers,
    )
    assert completed.json() == {
        "accepted": True,
        "state": "accepted",
        "comment_state": "visible_confirmed",
    }


def test_mobile_brand_comment_pull_honors_global_pause(tmp_path: Path) -> None:
    api = client(tmp_path)
    sessions = api.app.state.comment_sessions
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video(
        "https://www.tiktok.com/@bag/video/7523456789012345678",
        creator_username="bag",
        caption_anchor="rare archive piece",
    )
    draft = sessions.save_candidate(
        video.video_id,
        CommentCandidate(
            "That structured shape changes the whole outfit",
            "这个有型的包型改变了整套穿搭",
            0,
            "zoey",
        ),
    )
    sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    pull = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_kind": "brand_comment",
        "limit": 1,
    }

    assert api.post("/api/control/pause").json() == {"control": "paused"}
    assert api.post("/api/mobile/pull", json=pull, headers=headers).json() == {
        "tasks": []
    }
    assert api.post("/api/control/resume").json() == {"control": "running"}
    assert (
        len(api.post("/api/mobile/pull", json=pull, headers=headers).json()["tasks"])
        == 1
    )


def test_mobile_heartbeat_authenticates_device_and_epoch(tmp_path: Path) -> None:
    api = client(tmp_path)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    payload = {
        "device_id": "device-1",
        "session_epoch": 1,
        "app_version": "1.0.0",
        "phase": "idle",
        "queue_depth": 0,
        "client_timestamp_ms": 11_000,
    }

    accepted = api.post("/api/mobile/heartbeat", json=payload, headers=headers)
    stale = api.post(
        "/api/mobile/heartbeat",
        json={**payload, "session_epoch": 2},
        headers=headers,
    )

    assert accepted.status_code == 200
    assert accepted.json() == {"accepted": True, "server_time_ms": 12_000}
    assert stale.status_code == 409
    assert stale.json() == {"error": "stale_session"}


def test_mobile_runs_one_read_only_reconciliation_after_uncertain_submit(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    sessions = api.app.state.comment_sessions
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video(
        "https://www.tiktok.com/@bag/video/7523456789012345678",
        creator_username="bag",
        caption_anchor="rare archive piece",
    )
    draft = sessions.save_candidate(
        video.video_id,
        CommentCandidate(
            "That structured shape changes the whole outfit ✨",
            "这个有型的包型改变了整套穿搭 ✨",
            1,
            "zoey",
        ),
    )
    sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    pull = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_kind": "brand_comment",
        "limit": 1,
    }
    task = api.post("/api/mobile/pull", json=pull, headers=headers).json()["tasks"][0]
    result = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_id": task["task_id"],
        "lease_id": task["lease_id"],
        "idempotency_key": "comment-result-1",
        "state": "uncertain",
        "phase": "comment_reconciling",
        "evidence": {"visible_confirmed": False},
    }

    first = api.post("/api/mobile/results", json=result, headers=headers)
    reconciliation = api.post("/api/mobile/pull", json=pull, headers=headers).json()[
        "tasks"
    ][0]
    second = api.post("/api/mobile/results", json=result, headers=headers)
    final_pull = api.post("/api/mobile/pull", json=pull, headers=headers)

    assert first.json()["comment_state"] == "uncertain"
    assert reconciliation["phase"] == "comment_reconciling"
    assert second.json()["comment_state"] == "uncertain"
    assert final_pull.json()["tasks"] == []
    assert sessions.attempt_count(1) == 1


def test_mobile_persists_brand_comment_failure_code_for_diagnostics(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    sessions = api.app.state.comment_sessions
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    video = sessions.add_video(
        "https://www.tiktok.com/@bag/video/7523456789012345678",
        caption_anchor="rare archive piece",
    )
    draft = sessions.save_candidate(
        video.video_id,
        candidate=CommentCandidate(
            "That structured shape changes the whole outfit ✨",
            "这个有型的包型改变了整套穿搭 ✨",
            1,
            "zoey",
        ),
    )
    plan = sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    task = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_kind": "brand_comment",
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]

    response = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "comment-result-1",
            "state": "uncertain",
            "phase": "comment_reconciling",
            "evidence": {
                "visible_confirmed": False,
                "code": "video_identity_mismatch",
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    with sessions.repository._connect_read_only() as connection:
        attempt = connection.execute(
            "SELECT error_code FROM comment_attempts WHERE plan_id = ?",
            (plan.plan_id,),
        ).fetchone()
    assert attempt["error_code"] == "video_identity_mismatch"


def test_mobile_skips_pre_submit_route_failure_without_burning_next_plan(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    sessions = api.app.state.comment_sessions
    sessions.save_persona("zoey", "account-1", "IKUN BAGS | ZOEY")
    for offset in range(2):
        video = sessions.add_video(
            str(7523456789012345678 + offset),
            caption_anchor=f"archive piece {offset}",
        )
        draft = sessions.save_candidate(
            video.video_id,
            candidate=CommentCandidate(
                f"That structured shape changes the whole outfit {offset}",
                f"这个有型的包型改变了整套穿搭 {offset}",
                0,
                "zoey",
            ),
        )
        sessions.approve_plan("account-1", video.video_id, draft.candidate_id)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    pull = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_kind": "brand_comment",
        "limit": 1,
    }
    first = api.post("/api/mobile/pull", json=pull, headers=headers).json()["tasks"][0]

    result = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": first["task_id"],
            "lease_id": first["lease_id"],
            "idempotency_key": "route-failed-1",
            "state": "deferred",
            "phase": "video_opening",
            "evidence": {"error_code": "comment_video_not_verified"},
        },
        headers=headers,
    )
    next_pull = api.post("/api/mobile/pull", json=pull, headers=headers)

    assert result.json()["comment_state"] == "skipped"
    assert sessions.plan(first["plan_id"]).state == "skipped"
    assert next_pull.json()["tasks"] == []


def test_mobile_heartbeat_rejects_wrong_device_token(tmp_path: Path) -> None:
    api = client(tmp_path)
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()

    response = api.post(
        "/api/mobile/heartbeat",
        json={
            "device_id": "device-2",
            "session_epoch": 1,
            "app_version": "1.0.0",
            "phase": "idle",
            "queue_depth": 0,
            "client_timestamp_ms": 11_000,
        },
        headers={"Authorization": f"Bearer {registered['access_token']}"},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_mobile_token"}


def test_mobile_pull_and_result_replay_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    pool = repo.import_pool(
        "targets.csv",
        "c" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 12.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    pulled = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 50,
        },
        headers=headers,
    )

    assert pulled.status_code == 200
    task = pulled.json()["tasks"][0]
    result = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_id": task["task_id"],
        "lease_id": task["lease_id"],
        "idempotency_key": "result-1",
        "state": "deferred",
        "phase": "profile_opening",
        "evidence": {"error_code": "network_lost"},
    }
    first = api.post("/api/mobile/results", json=result, headers=headers)
    replay = api.post("/api/mobile/results", json=result, headers=headers)

    assert first.json() == {"accepted": True, "state": "accepted"}
    assert replay.json() == {"accepted": False, "state": "duplicate"}
    assignment = repo.assignment(int(task["task_id"]))
    assert assignment.phase is AssignmentPhase.SKIPPED
    assert assignment.last_error_code == "network_lost"
    assert (
        api.post(
            "/api/mobile/pull",
            json={
                "device_id": "device-1",
                "session_epoch": 1,
                "round_id": round_id,
                "limit": 1,
            },
            headers=headers,
        ).json()["tasks"]
        == []
    )


def test_mobile_video_error_completes_but_preserves_confirmed_visit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    pool = repo.import_pool(
        "targets.csv",
        "e" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 2_000_000_000.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    task = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]
    common = {
        "device_id": "device-1",
        "session_epoch": 1,
        "task_id": task["task_id"],
        "lease_id": task["lease_id"],
    }
    profile = api.post(
        "/api/mobile/results",
        json={
            **common,
            "idempotency_key": "profile-1",
            "state": "completed",
            "phase": "identity_confirmed",
            "evidence": {
                "observed_username": "target_user",
                "access_state": "available",
                "following": 20,
                "followers": 10,
                "video_count": 2,
                "post_handles": ["post:0", "post:1"],
            },
        },
        headers=headers,
    )
    assert profile.status_code == 200
    before = repo.assignment(int(task["task_id"]))
    assert before.phase is AssignmentPhase.VIDEO_OPENING
    assert before.visit_confirmed_at_ms is not None

    failed = api.post(
        "/api/mobile/results",
        json={
            **common,
            "idempotency_key": "video-1",
            "state": "deferred",
            "phase": "video_opening",
            "evidence": {"code": "missing_post_handle"},
        },
        headers=headers,
    )

    assert failed.json() == {"accepted": True, "state": "accepted"}
    after = repo.assignment(int(task["task_id"]))
    assert after.phase is AssignmentPhase.COMPLETED
    assert after.visit_confirmed_at_ms == before.visit_confirmed_at_ms
    assert after.last_error_code == "missing_post_handle"
    assert (
        api.post(
            "/api/mobile/pull",
            json={
                "device_id": "device-1",
                "session_epoch": 1,
                "round_id": round_id,
                "limit": 1,
            },
            headers=headers,
        ).json()["tasks"]
        == []
    )


def test_mobile_profile_result_reuses_round_snapshot_across_devices(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    pool = repo.import_pool(
        "targets.csv",
        "f" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1", "device-2": "seed-2"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 2_000_000_000.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    evidence = {
        "observed_username": "target_user",
        "access_state": "available",
        "following": 20,
        "followers": 10,
        "video_count": 2,
        "post_handles": ["post:0", "post:1"],
    }

    for number in (1, 2):
        device_id = f"device-{number}"
        registered = api.post(
            "/api/mobile/register",
            json={"device_id": device_id, "account_id": f"account-{number}"},
            headers={"Authorization": "Bearer bootstrap-secret"},
        ).json()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}
        task = api.post(
            "/api/mobile/pull",
            json={
                "device_id": device_id,
                "session_epoch": 1,
                "round_id": round_id,
                "limit": 1,
            },
            headers=headers,
        ).json()["tasks"][0]
        response = api.post(
            "/api/mobile/results",
            json={
                "device_id": device_id,
                "session_epoch": 1,
                "task_id": task["task_id"],
                "lease_id": task["lease_id"],
                "idempotency_key": f"profile-{number}",
                "state": "completed",
                "phase": "identity_confirmed",
                "evidence": (
                    evidence if number == 1 else {**evidence, "post_handles": []}
                ),
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json() == {"accepted": True, "state": "accepted"}
        if number == 1:
            finished = api.post(
                "/api/mobile/results",
                json={
                    "device_id": device_id,
                    "session_epoch": 1,
                    "task_id": task["task_id"],
                    "lease_id": task["lease_id"],
                    "idempotency_key": "video-1",
                    "state": "deferred",
                    "phase": "video_opening",
                    "evidence": {"code": "video_not_verified"},
                },
                headers=headers,
            )
            assert finished.status_code == 200
        else:
            assignment = repo.assignment(int(task["task_id"]))
            assert assignment.phase is AssignmentPhase.COMPLETED
            assert assignment.last_error_code == "profile_post_handles_incomplete"


def test_mobile_profile_result_creates_video_bound_follow_up_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO sqlite_sequence(name, seq) "
            "VALUES ('round_assignments', 32809)"
        )
    pool = repo.import_pool(
        "targets.csv",
        "d" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 2_000_000_000.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    task = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]

    response = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "profile-1",
            "state": "completed",
            "phase": "identity_confirmed",
            "evidence": {
                "observed_username": "target_user",
                "access_state": "available",
                "following": 20,
                "followers": 10,
                "video_count": 2,
                "post_handles": ["post:0", "post:1"],
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    assignment = repo.assignment(int(task["task_id"]))
    plan = repo.action_plan(round_id, assignment.identity_key, "device-1")
    assert assignment.phase is AssignmentPhase.VIDEO_OPENING
    assert plan is not None and plan.video_key in {"post:0", "post:1"}
    assert plan.effective_outcome is OutcomeKind.FAVORITE
    assert plan.state is ActionPlanState.PLANNED
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE round_assignments SET lease_expires_at_ms=1 WHERE assignment_id=?",
            (int(task["task_id"]),),
        )

    follow_up = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"]
    assert len(follow_up) == 1
    assert follow_up[0]["task_id"] == task["task_id"]
    assert follow_up[0]["video_key"] == plan.video_key
    assert follow_up[0]["action"] == "favorite"
    assert repo.assignment(int(task["task_id"])).phase is AssignmentPhase.VIDEO_OPENING

    uncertain = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "action-uncertain-1",
            "state": "uncertain",
            "phase": "action_executing",
            "evidence": {"code": "action_unverified", "plan_id": plan.plan_id},
        },
        headers=headers,
    )

    assert uncertain.status_code == 200
    terminal = repo.assignment(int(task["task_id"]))
    assert terminal.phase is AssignmentPhase.COMPLETED
    assert terminal.last_error_code == "action_uncertain_terminal"
    assert repo.action_plan_by_id(plan.plan_id).state is ActionPlanState.UNCERTAIN
    assert (
        api.post(
            "/api/mobile/pull",
            json={
                "device_id": "device-1",
                "session_epoch": 1,
                "round_id": round_id,
                "limit": 1,
            },
            headers=headers,
        ).json()["tasks"]
        == []
    )


def test_mobile_ineligible_profile_confirms_trace_and_completes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mobile.db"
    repo = AcquisitionRepository(path)
    repo.migrate()
    pool = repo.import_pool(
        "targets.csv",
        "f" * 64,
        (
            Target(
                target_id="target-1",
                username="target_user",
                profile_url="https://www.tiktok.com/@target_user",
                source_video_id="video-1",
                sec_uid="sec-1",
                identity_key="sec:sec-1",
                source_line_numbers=(2,),
            ),
        ),
    )
    round_id = create_exposure_round(
        repo,
        pool_id=pool.pool_id,
        device_seeds={"device-1": "seed-1"},
        starts_at_ms=0,
        min_inter_device_gap_ms=0,
        min_repeat_gap_ms=0,
    )
    api = TestClient(
        create_app(
            path,
            clock=lambda: 2_000_000_000.0,
            mobile_bootstrap_token="bootstrap-secret",
        )
    )
    registered = api.post(
        "/api/mobile/register",
        json={"device_id": "device-1", "account_id": "account-1"},
        headers={"Authorization": "Bearer bootstrap-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    task = api.post(
        "/api/mobile/pull",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "round_id": round_id,
            "limit": 1,
        },
        headers=headers,
    ).json()["tasks"][0]

    response = api.post(
        "/api/mobile/results",
        json={
            "device_id": "device-1",
            "session_epoch": 1,
            "task_id": task["task_id"],
            "lease_id": task["lease_id"],
            "idempotency_key": "profile-1",
            "state": "completed",
            "phase": "identity_confirmed",
            "evidence": {
                "observed_username": "target_user",
                "access_state": "available",
                "following": 10,
                "followers": 20,
                "video_count": 0,
                "post_handles": [],
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    assignment = repo.assignment(int(task["task_id"]))
    plan = repo.action_plan(round_id, assignment.identity_key, "device-1")
    assert assignment.phase is AssignmentPhase.COMPLETED
    assert plan is not None
    assert plan.effective_outcome is OutcomeKind.TRACE
    assert plan.state is ActionPlanState.CONFIRMED
