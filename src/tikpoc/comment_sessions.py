from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .acquisition_db import AcquisitionRepository
from .hot_comment_planner import (
    CommentCandidate,
    CommentEvidence,
    canonical_video_id,
    validate_candidate,
)


@dataclass(frozen=True)
class CommentVideo:
    video_id: str
    source_url: str
    creator_username: str = ""
    caption_anchor: str = ""


@dataclass(frozen=True)
class SavedCandidate:
    candidate_id: int
    video_id: str


@dataclass(frozen=True)
class CommentPlan:
    plan_id: int
    video_id: str
    account_id: str
    persona_id: str
    english: str
    chinese: str
    state: str


class CommentSessionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        *,
        clock_ms: Callable[[], int],
        daily_limit: int = 20,
    ) -> None:
        if daily_limit <= 0:
            raise ValueError("daily_limit must be positive")
        self.repository = repository
        self.clock_ms = clock_ms
        self.daily_limit = daily_limit

    def table_names(self) -> tuple[str, ...]:
        with self.repository._connect_read_only() as connection:
            return tuple(
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                )
            )

    def add_video(
        self,
        source: str,
        *,
        creator_username: str = "",
        caption_anchor: str = "",
    ) -> CommentVideo:
        video_id = canonical_video_id(source)
        source_url = str(source).strip()
        creator = str(creator_username).strip().removeprefix("@").casefold()
        anchor = str(caption_anchor).strip()
        now_ms = self.clock_ms()
        with self.repository._connect() as connection:
            connection.execute(
                """
                INSERT INTO comment_videos(
                    video_id, source_url, creator_username, caption_anchor,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    creator_username = CASE
                        WHEN excluded.creator_username <> ''
                        THEN excluded.creator_username
                        ELSE comment_videos.creator_username
                    END,
                    caption_anchor = CASE
                        WHEN excluded.caption_anchor <> ''
                        THEN excluded.caption_anchor
                        ELSE comment_videos.caption_anchor
                    END
                """,
                (video_id, source_url, creator, anchor, now_ms),
            )
            row = connection.execute(
                "SELECT * FROM comment_videos WHERE video_id = ?", (video_id,)
            ).fetchone()
        return self._video(row)

    def video(self, video_id: str) -> CommentVideo:
        canonical = canonical_video_id(video_id)
        with self.repository._connect_read_only() as connection:
            row = connection.execute(
                "SELECT * FROM comment_videos WHERE video_id = ?", (canonical,)
            ).fetchone()
        if row is None:
            raise ValueError("video_not_found")
        return self._video(row)

    @staticmethod
    def _video(row: sqlite3.Row) -> CommentVideo:
        return CommentVideo(
            str(row["video_id"]),
            str(row["source_url"]),
            str(row["creator_username"]),
            str(row["caption_anchor"]),
        )

    def import_evidence(
        self, video_id: str, evidence: Iterable[CommentEvidence]
    ) -> int:
        imported = 0
        with self.repository._connect() as connection:
            for item in evidence:
                cursor = connection.execute(
                    """
                    INSERT INTO comment_evidence(
                        cid, video_id, text, likes, replies, created_at,
                        language, imported_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cid) DO NOTHING
                    """,
                    (
                        item.cid,
                        canonical_video_id(video_id),
                        item.text,
                        max(0, item.likes),
                        max(0, item.replies),
                        item.created_at,
                        item.language,
                        self.clock_ms(),
                    ),
                )
                imported += cursor.rowcount
        return imported

    def save_persona(self, persona_id: str, account_id: str, display_name: str) -> None:
        values = tuple(
            str(value).strip() for value in (persona_id, account_id, display_name)
        )
        if not all(values):
            raise ValueError("persona fields are required")
        with self.repository._connect() as connection:
            connection.execute(
                """
                INSERT INTO comment_personas(
                    persona_id, account_id, display_name, created_at_ms
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(persona_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    display_name = excluded.display_name
                """,
                (*values, self.clock_ms()),
            )

    def save_candidate(
        self,
        video_id: str,
        candidate: CommentCandidate,
        *,
        command_id: str | None = None,
    ) -> SavedCandidate:
        canonical = canonical_video_id(video_id)
        with self.repository._connect() as connection:
            normalized_command = (
                str(command_id).strip() if command_id is not None else None
            )
            if normalized_command:
                existing = connection.execute(
                    "SELECT plan_id, video_id FROM comment_plans WHERE command_id = ?",
                    (normalized_command,),
                ).fetchone()
                if existing is not None:
                    if str(existing["video_id"]) != canonical:
                        raise ValueError("command_id_conflict")
                    return SavedCandidate(int(existing["plan_id"]), canonical)
            evidence = [
                CommentEvidence(
                    str(row["cid"]),
                    str(row["text"]),
                    int(row["likes"]),
                    int(row["replies"]),
                    int(row["created_at"]),
                    str(row["language"]),
                )
                for row in connection.execute(
                    "SELECT * FROM comment_evidence WHERE video_id = ?", (canonical,)
                )
            ]
            validate_candidate(candidate, evidence=evidence)
            now_ms = self.clock_ms()
            cursor = connection.execute(
                """
                INSERT INTO comment_plans(
                    command_id, video_id, persona_id, account_id, english, chinese,
                    emoji_count, state, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    normalized_command,
                    canonical,
                    candidate.persona_id,
                    candidate.english.strip(),
                    candidate.chinese.strip(),
                    candidate.emoji_count,
                    now_ms,
                    now_ms,
                ),
            )
        return SavedCandidate(int(cursor.lastrowid), canonical)

    def approve_plan(
        self, account_id: str, video_id: str, candidate_id: int
    ) -> CommentPlan:
        canonical = canonical_video_id(video_id)
        now_ms = self.clock_ms()
        try:
            with self.repository._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                owner = connection.execute(
                    """
                    SELECT persona.account_id
                    FROM comment_plans AS plan
                    JOIN comment_personas AS persona
                      ON persona.persona_id = plan.persona_id
                    WHERE plan.plan_id = ? AND plan.video_id = ?
                    """,
                    (candidate_id, canonical),
                ).fetchone()
                if owner is None:
                    raise ValueError("candidate_not_approvable")
                if str(owner["account_id"]) != str(account_id).strip():
                    raise ValueError("persona_account_mismatch")
                existing = connection.execute(
                    "SELECT * FROM comment_plans WHERE plan_id = ?",
                    (candidate_id,),
                ).fetchone()
                if (
                    existing is not None
                    and str(existing["state"]) != "draft"
                    and str(existing["account_id"]) == str(account_id).strip()
                ):
                    return self._plan(existing)
                cursor = connection.execute(
                    """
                    UPDATE comment_plans
                    SET account_id = ?, state = 'approved', approved_at_ms = ?,
                        updated_at_ms = ?
                    WHERE plan_id = ? AND video_id = ? AND state = 'draft'
                    """,
                    (account_id, now_ms, now_ms, candidate_id, canonical),
                )
                if cursor.rowcount != 1:
                    raise ValueError("candidate_not_approvable")
        except sqlite3.IntegrityError as error:
            raise ValueError("approved_plan_exists") from error
        return self.plan(candidate_id)

    def plan(self, plan_id: int) -> CommentPlan:
        with self.repository._connect_read_only() as connection:
            row = connection.execute(
                "SELECT * FROM comment_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None or row["account_id"] is None:
            raise ValueError("plan_not_found")
        return self._plan(row)

    def claim_for_account(
        self,
        account_id: str,
        worker_id: str,
        *,
        include_reconciliation: bool = False,
    ) -> CommentPlan | None:
        now_ms = self.clock_ms()
        start_ms, end_ms = _local_day_bounds(now_ms)
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            busy = connection.execute(
                """
                SELECT * FROM comment_plans
                WHERE account_id = ? AND state IN ('leased','submitted','uncertain')
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if busy is not None:
                if include_reconciliation and str(busy["state"]) in {
                    "submitted",
                    "uncertain",
                }:
                    return self._plan(busy)
                return None
            used = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM comment_attempts AS attempt
                JOIN comment_plans AS plan ON plan.plan_id = attempt.plan_id
                WHERE plan.account_id = ?
                  AND attempt.submitted_at_ms >= ? AND attempt.submitted_at_ms < ?
                  AND attempt.state IN ('submitted','uncertain','visible_confirmed')
                """,
                (account_id, start_ms, end_ms),
            ).fetchone()
            if int(used["count"]) >= self.daily_limit:
                return None
            row = connection.execute(
                """
                SELECT * FROM comment_plans
                WHERE account_id = ? AND state = 'approved'
                ORDER BY approved_at_ms, plan_id
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE comment_plans
                SET state = 'leased', lease_owner = ?, updated_at_ms = ?
                WHERE plan_id = ? AND state = 'approved'
                """,
                (worker_id, now_ms, int(row["plan_id"])),
            )
            updated = dict(row)
            updated["state"] = "leased"
            updated["lease_owner"] = worker_id
        return self._plan(updated)

    def list_plans(self) -> list[dict[str, object]]:
        with self.repository._connect_read_only() as connection:
            rows = connection.execute(
                """
                SELECT plan_id, video_id, persona_id, account_id, state,
                       created_at_ms, approved_at_ms, updated_at_ms
                FROM comment_plans WHERE state <> 'draft' OR account_id IS NOT NULL
                ORDER BY plan_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def record_submission(
        self, plan_id: int, idempotency_key: str, *, state: str
    ) -> None:
        if state not in {"submitted", "uncertain", "visible_confirmed"}:
            raise ValueError("invalid_submission_state")
        now_ms = self.clock_ms()
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO comment_attempts(
                    plan_id, idempotency_key, state, submitted_at_ms
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (plan_id, idempotency_key, state, now_ms),
            )
            attempt = connection.execute(
                "SELECT plan_id FROM comment_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if attempt is None or int(attempt["plan_id"]) != plan_id:
                raise ValueError("idempotency_key_conflict")
            connection.execute(
                """
                UPDATE comment_plans
                SET state = CASE
                        WHEN state = 'visible_confirmed' THEN state ELSE ? END,
                    updated_at_ms = ?
                WHERE plan_id = ? AND state IN ('leased','submitted','uncertain')
                """,
                (state, now_ms, plan_id),
            )

    def record_reconciliation(
        self, plan_id: int, idempotency_key: str, *, visible: bool
    ) -> None:
        state = "visible_confirmed" if visible else "uncertain"
        now_ms = self.clock_ms()
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE comment_attempts
                SET state = CASE
                        WHEN state = 'visible_confirmed' THEN state ELSE ? END,
                    reconciled_at_ms = ?
                WHERE plan_id = ? AND idempotency_key = ?
                """,
                (state, now_ms, plan_id, idempotency_key),
            )
            connection.execute(
                """
                UPDATE comment_plans
                SET state = CASE
                        WHEN state = 'visible_confirmed' THEN state ELSE ? END,
                    updated_at_ms = ?
                WHERE plan_id = ? AND state IN ('submitted','uncertain','visible_confirmed')
                """,
                (state, now_ms, plan_id),
            )

    def record_observation(self, plan_id: int, *, likes: int, replies: int) -> None:
        with self.repository._connect() as connection:
            connection.execute(
                """
                INSERT INTO comment_observations(
                    plan_id, likes, replies, observed_at_ms
                ) VALUES (?, ?, ?, ?)
                """,
                (plan_id, max(0, likes), max(0, replies), self.clock_ms()),
            )

    def record_verification_required(
        self,
        device_id: str,
        account_id: str,
        plan_id: int,
        *,
        phase: str,
        event_key: str | None = None,
    ) -> None:
        now_ms = self.clock_ms()
        values = (str(device_id).strip(), str(account_id).strip(), int(plan_id), phase)
        if not all((values[0], values[1], values[3])) or values[2] <= 0:
            raise ValueError("invalid_verification_event")
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO comment_device_blocks(
                    device_id, account_id, plan_id, phase, state,
                    blocked_at_ms, acknowledged_at_ms
                ) VALUES (?, ?, ?, ?, 'verification_required', ?, NULL)
                ON CONFLICT(device_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    plan_id = excluded.plan_id,
                    phase = excluded.phase,
                    state = 'verification_required',
                    command_id = NULL,
                    blocked_at_ms = excluded.blocked_at_ms,
                    acknowledged_at_ms = NULL
                """,
                (*values, now_ms),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO comment_recovery_events(
                    device_id, account_id, plan_id, phase, event_type,
                    command_id, occurred_at_ms
                ) VALUES (?, ?, ?, ?, 'verification_required', ?, ?)
                """,
                (*values, event_key, now_ms),
            )

    def acknowledge_recovery(
        self, device_id: str, *, command_id: str
    ) -> dict[str, object]:
        device_id = str(device_id).strip()
        command_id = str(command_id).strip()
        if not device_id or not command_id:
            raise ValueError("recovery identifiers are required")
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                """
                SELECT * FROM comment_recovery_events
                WHERE command_id = ? AND event_type = 'recovery_acknowledged'
                """,
                (command_id,),
            ).fetchone()
            if replay is not None:
                return _recovery_payload(replay, "recovery_requested")
            block = connection.execute(
                "SELECT * FROM comment_device_blocks WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if block is None:
                raise ValueError("verification_block_not_found")
            now_ms = self.clock_ms()
            connection.execute(
                """
                UPDATE comment_device_blocks
                SET state = 'recovery_requested', command_id = ?, acknowledged_at_ms = ?
                WHERE device_id = ?
                """,
                (command_id, now_ms, device_id),
            )
            connection.execute(
                """
                INSERT INTO comment_recovery_events(
                    device_id, account_id, plan_id, phase, event_type,
                    command_id, occurred_at_ms
                ) VALUES (?, ?, ?, ?, 'recovery_acknowledged', ?, ?)
                """,
                (
                    device_id,
                    str(block["account_id"]),
                    int(block["plan_id"]),
                    str(block["phase"]),
                    command_id,
                    now_ms,
                ),
            )
            event = connection.execute(
                "SELECT * FROM comment_recovery_events WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return _recovery_payload(event, "recovery_requested")

    def complete_stable_home(self, device_id: str, account_id: str) -> bool:
        with self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            block = connection.execute(
                """
                SELECT * FROM comment_device_blocks
                WHERE device_id = ? AND account_id = ?
                  AND state = 'recovery_requested'
                """,
                (device_id, account_id),
            ).fetchone()
            if block is None:
                return self.device_block(device_id) is None
            now_ms = self.clock_ms()
            connection.execute(
                """
                INSERT INTO comment_recovery_events(
                    device_id, account_id, plan_id, phase, event_type, occurred_at_ms
                ) VALUES (?, ?, ?, ?, 'stable_home', ?)
                """,
                (
                    device_id,
                    account_id,
                    int(block["plan_id"]),
                    str(block["phase"]),
                    now_ms,
                ),
            )
            connection.execute(
                "DELETE FROM comment_device_blocks WHERE device_id = ?", (device_id,)
            )
        return True

    def device_block(self, device_id: str) -> dict[str, object] | None:
        with self.repository._connect_read_only() as connection:
            row = connection.execute(
                "SELECT * FROM comment_device_blocks WHERE device_id = ?", (device_id,)
            ).fetchone()
        return None if row is None else dict(row)

    def metrics(self, account_id: str) -> dict[str, int]:
        with self.repository._connect_read_only() as connection:
            plan = connection.execute(
                """
                SELECT COUNT(*) AS planned,
                       SUM(CASE WHEN state = 'visible_confirmed' THEN 1 ELSE 0 END)
                         AS visible_confirmed,
                       SUM(CASE WHEN state = 'uncertain' THEN 1 ELSE 0 END) AS uncertain
                FROM comment_plans WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            submitted = connection.execute(
                """
                SELECT COUNT(*) AS count FROM comment_attempts AS attempt
                JOIN comment_plans AS plan ON plan.plan_id = attempt.plan_id
                WHERE plan.account_id = ?
                """,
                (account_id,),
            ).fetchone()
            observations = connection.execute(
                """
                SELECT COALESCE(SUM(observation.likes), 0) AS likes,
                       COALESCE(SUM(observation.replies), 0) AS replies
                FROM comment_observations AS observation
                JOIN comment_plans AS plan ON plan.plan_id = observation.plan_id
                WHERE plan.account_id = ?
                """,
                (account_id,),
            ).fetchone()
            verification = connection.execute(
                """
                SELECT COUNT(*) AS count FROM comment_recovery_events
                WHERE account_id = ? AND event_type = 'verification_required'
                """,
                (account_id,),
            ).fetchone()
        return {
            "planned": int(plan["planned"] or 0),
            "submitted": int(submitted["count"] or 0),
            "visible_confirmed": int(plan["visible_confirmed"] or 0),
            "uncertain": int(plan["uncertain"] or 0),
            "verification_required": int(verification["count"] or 0),
            "observed_likes": int(observations["likes"] or 0),
            "observed_replies": int(observations["replies"] or 0),
        }

    def attempt_count(self, plan_id: int) -> int:
        return self._count("comment_attempts", plan_id)

    def observation_count(self, plan_id: int) -> int:
        return self._count("comment_observations", plan_id)

    def _count(self, table: str, plan_id: int) -> int:
        with self.repository._connect_read_only() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _plan(row: sqlite3.Row | dict[str, object]) -> CommentPlan:
        return CommentPlan(
            int(row["plan_id"]),
            str(row["video_id"]),
            str(row["account_id"]),
            str(row["persona_id"]),
            str(row["english"]),
            str(row["chinese"]),
            str(row["state"]),
        )


def _local_day_bounds(now_ms: int) -> tuple[int, int]:
    timezone = ZoneInfo("Asia/Shanghai")
    current = datetime.fromtimestamp(now_ms / 1000, tz=timezone)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _recovery_payload(row: sqlite3.Row, state: str) -> dict[str, object]:
    return {
        "device_id": str(row["device_id"]),
        "account_id": str(row["account_id"]),
        "plan_id": int(row["plan_id"]),
        "state": state,
        "command_id": str(row["command_id"]),
    }
