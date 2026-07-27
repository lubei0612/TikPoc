import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .acquisition_db import AcquisitionRepository
from .acquisition_errors import AcquisitionConflict, AcquisitionNotFound
from .importer import read_targets
from .operator_control_repository import OperatorControlRepository
from .rounds import create_exposure_round
from .web_accounts import WebAccount

_QUOTA_LIMITS = {"like": 100, "favorite": 14, "repost": 25}
_MAX_DIAGNOSTIC_SCREENSHOT_BYTES = 10 * 1024 * 1024
_SCREENSHOT_MEDIA_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_BROWSER_PAGE_ROLES = ("activity", "messages")
_BROWSER_HEARTBEAT_STALE_MS = 120_000
_MOBILE_PHASE_STALL_MS = 120_000
_MOBILE_PROGRESS_STALE_MS = 300_000


def merge_browser_health_rows(
    accounts: Sequence[WebAccount],
    stored_rows: Sequence[Mapping[str, object]],
    *,
    now_ms: int,
) -> list[dict[str, object]]:
    stored = {
        (str(row["account_id"]), str(row["page_role"])): row for row in stored_rows
    }
    identities = (
        [
            (
                account.account_id,
                account.device_id,
                account.browser_profile_label,
                account.expected_tiktok_username,
                role,
            )
            for account in accounts
            for role in _BROWSER_PAGE_ROLES
        ]
        if accounts
        else [
            (
                account_id,
                str(row.get("device_id") or ""),
                "",
                "",
                role,
            )
            for (account_id, role), row in sorted(stored.items())
        ]
    )

    result: list[dict[str, object]] = []
    for account_id, device_id, profile_label, expected_username, role in identities:
        row = stored.get((account_id, role), {})
        observed_at_ms = int(row.get("observed_at_ms") or 0)
        last_scan_at_ms = int(row.get("last_scan_at_ms") or 0)
        last_success_at_ms = int(row.get("last_success_at_ms") or 0)
        scan_state = str(row.get("scan_state") or "not_started")
        stored_state = str(row.get("status") or "unbound")
        if stored_state == "healthy":
            stored_state = "ready"
        if accounts and not expected_username or not row:
            binding_state = "unbound"
        elif (
            (
                observed_at_ms
                and int(now_ms) - observed_at_ms > _BROWSER_HEARTBEAT_STALE_MS
            )
            or stored_state == "ready"
            and (
                not last_success_at_ms
                or int(now_ms) - last_success_at_ms > _BROWSER_HEARTBEAT_STALE_MS
            )
        ):
            binding_state = "stale"
        else:
            binding_state = stored_state
        result.append(
            {
                "account_id": account_id,
                "page_role": role,
                "device_id": device_id,
                "browser_profile_label": profile_label,
                "expected_tiktok_username": expected_username,
                "observed_username": str(row.get("observed_username") or ""),
                "binding_state": binding_state,
                "status": binding_state,
                "observed_at_ms": observed_at_ms,
                "last_scan_at_ms": last_scan_at_ms,
                "last_success_at_ms": last_success_at_ms,
                "scan_state": scan_state,
                "detail": str(row.get("detail") or ""),
            }
        )
    return result


def _is_supported_image(path: Path, suffix: str) -> bool:
    try:
        content = path.read_bytes()
    except OSError:
        return False
    if suffix == ".png":
        return (
            len(content) >= 45
            and content.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            and content[-12:-8] == b"\x00\x00\x00\x00"
            and content[-8:-4] == b"IEND"
        )
    if suffix in {".jpeg", ".jpg"}:
        return (
            len(content) >= 4
            and content.startswith(b"\xff\xd8\xff")
            and content.endswith(b"\xff\xd9")
        )
    if suffix == ".webp":
        return (
            len(content) >= 20
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
            and content[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
            and int.from_bytes(content[4:8], "little") + 8 == len(content)
        )
    return False


class AcquisitionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        *,
        clock_ms: Callable[[], int],
        import_roots: Sequence[Path],
        browser_accounts: Sequence[WebAccount] = (),
    ) -> None:
        self.repository = repository
        self.path = repository.path
        self.clock_ms = clock_ms
        self.import_roots = tuple(root.resolve() for root in import_roots)
        self.browser_accounts = tuple(browser_accounts)
        self.operator_control = OperatorControlRepository(self.path, clock_ms=clock_ms)

    def migrate(self) -> None:
        self.operator_control.migrate()

    def pools(self, *, offset: int, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            total = int(
                connection.execute("SELECT COUNT(*) FROM target_pools").fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT pool_id, source_name, unique_targets, source_rows, created_at_ms
                FROM target_pools ORDER BY created_at_ms DESC, pool_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def rounds(self, *, offset: int, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            total = int(
                connection.execute("SELECT COUNT(*) FROM exposure_rounds").fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT round.round_id, round.pool_id, round.state, round.starts_at_ms,
                       round.created_at_ms, pool.unique_targets AS target_count,
                       COUNT(seed.device_id) AS device_count
                FROM exposure_rounds AS round
                JOIN target_pools AS pool ON pool.pool_id = round.pool_id
                LEFT JOIN round_device_seeds AS seed ON seed.round_id = round.round_id
                GROUP BY round.round_id
                ORDER BY round.created_at_ms DESC, round.round_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def import_pool(self, local_path: str) -> dict[str, object]:
        source = Path(local_path).expanduser().resolve()
        if not any(source.is_relative_to(root) for root in self.import_roots):
            raise AcquisitionConflict("import path is outside configured roots")
        if not source.is_file() or source.suffix.lower() not in {
            ".csv",
            ".xlsx",
            ".xlsm",
        }:
            raise AcquisitionConflict("import source is not a supported file")
        imported_targets = read_targets(source)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        imported = self.repository.import_pool(
            source.name, checksum, imported_targets.targets
        )
        return {
            "pool_id": imported.pool_id,
            "unique_targets": imported.unique_targets,
            "source_rows": imported.source_rows,
            "skipped_duplicates": imported_targets.skipped_duplicates,
            "skipped_invalid": imported_targets.skipped_invalid,
        }

    def create_round(
        self,
        *,
        pool_id: str,
        device_seeds: Mapping[str, str],
        starts_at_ms: int,
        min_inter_device_gap_ms: int,
        min_repeat_gap_ms: int,
    ) -> dict[str, object]:
        if not self.repository.pool_exists(pool_id):
            raise AcquisitionNotFound(pool_id)
        round_id = create_exposure_round(
            self.repository,
            pool_id=pool_id,
            device_seeds=device_seeds,
            starts_at_ms=starts_at_ms,
            min_inter_device_gap_ms=min_inter_device_gap_ms,
            min_repeat_gap_ms=min_repeat_gap_ms,
        )
        return {
            "round_id": round_id,
            "pool_id": pool_id,
            "device_count": len(device_seeds),
            "assignment_count": self.repository.assignment_count(round_id),
        }

    def operations(self, round_id: str) -> dict[str, object]:
        with self._read_connection() as connection:
            round_row = connection.execute(
                """
                SELECT round.round_id, round.pool_id, round.state,
                       round.starts_at_ms, pool.unique_targets AS target_count
                FROM exposure_rounds AS round
                JOIN target_pools AS pool ON pool.pool_id = round.pool_id
                WHERE round.round_id = ?
                """,
                (round_id,),
            ).fetchone()
            if round_row is None:
                raise AcquisitionNotFound(round_id)
            device_rows = self._device_rows(connection, round_id)
            coverage = self._coverage_summary(connection, round_id)
            quotas = self._quota_rows(connection, round_id)
            recent_traces = self._recent_traces(connection, round_id, limit=100)
            diagnostics = self._latest_diagnostics(connection, round_id)
            browser_health = self._browser_health_rows(connection)
        for device in device_rows:
            device["latest_diagnostic"] = diagnostics.get(str(device["device_id"]))
        return {
            "round": dict(round_row),
            "devices": device_rows,
            "quotas": quotas,
            "coverage": coverage,
            "recent_mobile_traces": recent_traces,
            "browser_health": browser_health,
        }

    def coverage(self, round_id: str, *, offset: int, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            round_row = connection.execute(
                "SELECT pool_id FROM exposure_rounds WHERE round_id = ?", (round_id,)
            ).fetchone()
            if round_row is None:
                raise AcquisitionNotFound(round_id)
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pool_targets WHERE pool_id = ?",
                    (str(round_row["pool_id"]),),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT assignment.assignment_id, assignment.identity_key,
                       target.username, assignment.device_id, assignment.phase,
                       assignment.attempt_count, assignment.next_attempt_at_ms,
                       assignment.visit_confirmed_at_ms,
                       assignment.completed_at_ms, assignment.last_error_code,
                       plan.effective_outcome,
                       COALESCE(control.state, 'running') AS control_state
                FROM round_assignments AS assignment
                JOIN exposure_rounds AS round ON round.round_id = assignment.round_id
                JOIN pool_targets AS target
                  ON target.pool_id = round.pool_id
                 AND target.identity_key = assignment.identity_key
                LEFT JOIN device_action_plans AS plan
                  ON plan.round_id = assignment.round_id
                 AND plan.identity_key = assignment.identity_key
                 AND plan.device_id = assignment.device_id
                LEFT JOIN operator_control_states AS control
                  ON control.scope = 'assignment'
                 AND control.scope_id = CAST(assignment.assignment_id AS TEXT)
                WHERE assignment.round_id = ?
                  AND assignment.identity_key IN (
                      SELECT identity_key FROM pool_targets WHERE pool_id = ?
                      ORDER BY ordinal LIMIT ? OFFSET ?
                  )
                ORDER BY target.ordinal, assignment.device_id
                """,
                (round_id, str(round_row["pool_id"]), limit, offset),
            ).fetchall()
        items_by_identity: dict[str, dict[str, object]] = {}
        for row in rows:
            identity_key = str(row["identity_key"])
            item = items_by_identity.setdefault(
                identity_key,
                {
                    "identity_key": identity_key,
                    "username": str(row["username"]),
                    "devices": [],
                },
            )
            completed_at = row["completed_at_ms"]
            visited_at = row["visit_confirmed_at_ms"]
            duration_ms = (
                None
                if completed_at is None or visited_at is None
                else max(0, int(completed_at) - int(visited_at))
            )
            item["devices"].append(
                {
                    "assignment_id": int(row["assignment_id"]),
                    "device_id": str(row["device_id"]),
                    "phase": str(row["phase"]),
                    "planned_outcome": row["effective_outcome"],
                    "visit_confirmed": visited_at is not None,
                    "completed": str(row["phase"]) == "completed",
                    "duration_ms": duration_ms,
                    "attempt_count": int(row["attempt_count"]),
                    "next_attempt_at_ms": int(row["next_attempt_at_ms"]),
                    "last_error_code": row["last_error_code"],
                    "control_state": str(row["control_state"]),
                }
            )
        return {
            "round_id": round_id,
            "items": list(items_by_identity.values()),
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def apply_command(
        self, command_type: str, command_id: str, scope: str, scope_id: str
    ) -> dict[str, object]:
        return self.operator_control.apply_command(
            command_type, command_id, scope, scope_id
        )

    def retry(self, command_id: str, assignment_id: int) -> dict[str, object]:
        return self.operator_control.retry(command_id, assignment_id)

    def diagnostics(self, assignment_id: int, *, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            assignment = connection.execute(
                """
                SELECT assignment_id, round_id, identity_key, device_id, phase,
                       attempt_count, last_error_code
                FROM round_assignments WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
            if assignment is None:
                raise AcquisitionNotFound(str(assignment_id))
            rows = connection.execute(
                """
                SELECT attempt.attempt_id, attempt.attempt_index, attempt.result,
                       attempt.diagnostics_json, attempt.attempted_at_ms
                FROM action_attempts AS attempt
                JOIN device_action_plans AS plan ON plan.plan_id = attempt.plan_id
                JOIN round_assignments AS assignment
                  ON assignment.round_id = plan.round_id
                 AND assignment.identity_key = plan.identity_key
                 AND assignment.device_id = plan.device_id
                WHERE assignment.assignment_id = ?
                ORDER BY attempt.attempted_at_ms DESC, attempt.attempt_id DESC
                LIMIT ?
                """,
                (assignment_id, limit),
            ).fetchall()
        attempts = []
        for row in rows:
            try:
                diagnostic = json.loads(str(row["diagnostics_json"]))
            except (TypeError, json.JSONDecodeError):
                diagnostic = {}
            screenshot_path = str(diagnostic.get("screenshot_path") or "")
            attempts.append(
                {
                    "attempt_id": int(row["attempt_id"]),
                    "attempt_index": int(row["attempt_index"]),
                    "result": str(row["result"]),
                    "attempted_at_ms": int(row["attempted_at_ms"]),
                    "ui_summary": str(diagnostic.get("ui_summary") or "")[:500],
                    "screenshot_id": (
                        hashlib.sha256(screenshot_path.encode()).hexdigest()[:24]
                        if screenshot_path
                        else None
                    ),
                }
            )
        payload = dict(assignment)
        payload["attempts"] = attempts
        return payload

    def diagnostic_screenshot(self, screenshot_id: str) -> tuple[Path, str]:
        if not re.fullmatch(r"[0-9a-f]{24}", screenshot_id):
            raise AcquisitionNotFound(screenshot_id)
        evidence_root = (self.path.parent / "screenshots").resolve()
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT diagnostics_json FROM action_attempts"
            ).fetchall()
        for row in rows:
            try:
                diagnostic = json.loads(str(row["diagnostics_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
            raw_path = str(diagnostic.get("screenshot_path") or "")
            if not raw_path:
                continue
            candidate_id = hashlib.sha256(raw_path.encode()).hexdigest()[:24]
            if candidate_id != screenshot_id:
                continue
            candidate = Path(raw_path).expanduser().resolve()
            media_type = _SCREENSHOT_MEDIA_TYPES.get(candidate.suffix.lower())
            try:
                stat = candidate.stat()
            except OSError as error:
                raise AcquisitionNotFound(screenshot_id) from error
            if (
                not candidate.is_relative_to(evidence_root)
                or not candidate.is_file()
                or media_type is None
                or stat.st_size > _MAX_DIAGNOSTIC_SCREENSHOT_BYTES
                or not _is_supported_image(candidate, candidate.suffix.lower())
            ):
                raise AcquisitionNotFound(screenshot_id)
            return candidate, media_type
        raise AcquisitionNotFound(screenshot_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _read_connection(self) -> sqlite3.Connection:
        connection = self._connect()
        connection.execute("BEGIN")
        return connection

    def _device_rows(
        self, connection: sqlite3.Connection, round_id: str
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT seed.device_id, exposure_round.state AS round_state,
                   health.account_id, health.state AS health,
                   health.error_code, health.updated_at_ms,
                   COALESCE(device_control.state, 'running') AS control_state,
                   assignment.assignment_id, assignment.identity_key,
                   assignment.phase, assignment.attempt_count,
                   assignment.last_error_code, assignment.visit_confirmed_at_ms,
                   assignment.completed_at_ms,
                   (
                       SELECT MAX(history.changed_at_ms)
                       FROM assignment_phase_history AS history
                       WHERE history.assignment_id = assignment.assignment_id
                         AND history.to_phase = assignment.phase
                   ) AS current_phase_started_at_ms,
                   (
                       SELECT MAX(history.changed_at_ms)
                       FROM assignment_phase_history AS history
                       JOIN round_assignments AS progressed
                         ON progressed.assignment_id = history.assignment_id
                       WHERE progressed.round_id = seed.round_id
                         AND progressed.device_id = seed.device_id
                   ) AS last_progress_at_ms,
                   (
                       SELECT COUNT(*) FROM round_assignments AS remaining
                       WHERE remaining.round_id = seed.round_id
                         AND remaining.device_id = seed.device_id
                         AND remaining.phase NOT IN ('completed', 'skipped')
                   ) AS remaining_count,
                   COALESCE(assignment_control.state, 'running')
                       AS assignment_control_state
            FROM round_device_seeds AS seed
            JOIN exposure_rounds AS exposure_round
              ON exposure_round.round_id = seed.round_id
            LEFT JOIN fleet_device_health AS health ON health.device_id = seed.device_id
            LEFT JOIN operator_control_states AS device_control
              ON device_control.scope = 'device'
             AND device_control.scope_id = seed.device_id
            LEFT JOIN round_assignments AS assignment
              ON assignment.assignment_id = (
                  SELECT candidate.assignment_id FROM round_assignments AS candidate
                  WHERE candidate.round_id = seed.round_id
                    AND candidate.device_id = seed.device_id
                    AND candidate.phase IN (
                        'profile_opening', 'identity_confirmed', 'waiting_snapshot',
                        'video_opening', 'video_confirmed', 'quota_reserved',
                        'action_executing', 'action_reconciling'
                    )
                  ORDER BY candidate.assignment_id LIMIT 1
              )
            LEFT JOIN operator_control_states AS assignment_control
              ON assignment_control.scope = 'assignment'
             AND assignment_control.scope_id = CAST(assignment.assignment_id AS TEXT)
            WHERE seed.round_id = ? ORDER BY seed.device_id
            """,
            (round_id,),
        ).fetchall()
        duration_rows = connection.execute(
            """
            WITH first_claims AS (
                SELECT history.assignment_id,
                       MIN(history.changed_at_ms) AS started_at_ms
                FROM assignment_phase_history AS history
                JOIN round_assignments AS assignment
                  ON assignment.assignment_id = history.assignment_id
                WHERE assignment.round_id = ?
                  AND history.to_phase = 'profile_opening'
                GROUP BY history.assignment_id
            )
            SELECT assignment.device_id,
                   assignment.completed_at_ms - first_claims.started_at_ms
                       AS duration_ms
            FROM round_assignments AS assignment
            JOIN first_claims ON first_claims.assignment_id = assignment.assignment_id
            WHERE assignment.round_id = ? AND assignment.phase = 'completed'
              AND assignment.completed_at_ms > first_claims.started_at_ms
            ORDER BY assignment.device_id, duration_ms
            """,
            (round_id, round_id),
        ).fetchall()
        durations: dict[str, list[int]] = {}
        for row in duration_rows:
            durations.setdefault(str(row["device_id"]), []).append(
                int(row["duration_ms"])
            )
        now_ms = self.clock_ms()
        visit_times: dict[str, list[int]] = {}
        for row in connection.execute(
            """
            SELECT device_id, visit_confirmed_at_ms
            FROM round_assignments
            WHERE round_id = ? AND visit_confirmed_at_ms IS NOT NULL
            """,
            (round_id,),
        ):
            visit_times.setdefault(str(row["device_id"]), []).append(
                int(row["visit_confirmed_at_ms"])
            )
        stuck_by_device = {
            str(row["device_id"]): int(row["stuck_count"])
            for row in connection.execute(
                """
                SELECT assignment.device_id, COUNT(*) AS stuck_count
                FROM round_assignments AS assignment
                WHERE assignment.round_id = ?
                  AND assignment.phase NOT IN ('completed', 'skipped')
                  AND (
                    assignment.attempt_count > 1
                    OR ? - COALESCE((
                        SELECT MAX(history.changed_at_ms)
                        FROM assignment_phase_history AS history
                        WHERE history.assignment_id = assignment.assignment_id
                          AND history.to_phase = assignment.phase
                    ), ?) > ?
                  )
                GROUP BY assignment.device_id
                """,
                (round_id, now_ms, now_ms, 2 * _MOBILE_PHASE_STALL_MS),
            )
        }
        devices = []
        for row in rows:
            values = durations.get(str(row["device_id"]), [])
            mean_ms = 0.0 if not values else sum(values) / len(values)
            p50_ms = (
                0.0
                if not values
                else float(values[max(0, math.ceil(len(values) * 0.5) - 1)])
            )
            p90_ms = (
                0.0
                if not values
                else float(values[max(0, math.ceil(len(values) * 0.9) - 1)])
            )
            confirmed = visit_times.get(str(row["device_id"]), [])
            confirmed_visits_15m = sum(
                timestamp > now_ms - 15 * 60_000 for timestamp in confirmed
            )
            confirmed_visits_60m = sum(
                timestamp > now_ms - 60 * 60_000 for timestamp in confirmed
            )
            assignment = None
            if row["assignment_id"] is not None:
                assignment = {
                    "assignment_id": int(row["assignment_id"]),
                    "identity_key": str(row["identity_key"]),
                    "phase": str(row["phase"]),
                    "attempt_count": int(row["attempt_count"]),
                    "last_error_code": row["last_error_code"],
                    "control_state": str(row["assignment_control_state"]),
                }
            reported_health = str(row["health"] or "unknown")
            health = reported_health
            health_error_code = row["error_code"]
            monitoring_active = (
                str(row["round_state"]) == "running"
                and str(row["control_state"]) == "running"
                and str(row["assignment_control_state"]) == "running"
            )
            phase_started_at_ms = (
                None
                if row["current_phase_started_at_ms"] is None
                else int(row["current_phase_started_at_ms"])
            )
            last_progress_at_ms = (
                None
                if row["last_progress_at_ms"] is None
                else int(row["last_progress_at_ms"])
            )
            progress_values = [
                value
                for value in (
                    phase_started_at_ms,
                    last_progress_at_ms,
                    None if row["updated_at_ms"] is None else int(row["updated_at_ms"]),
                )
                if value is not None
            ]
            progress_age_ms = (
                0 if not progress_values else max(0, now_ms - max(progress_values))
            )
            if (
                monitoring_active
                and reported_health == "healthy"
                and assignment is not None
            ):
                phase_age_ms = (
                    0
                    if phase_started_at_ms is None
                    else max(0, now_ms - phase_started_at_ms)
                )
                if phase_age_ms >= _MOBILE_PHASE_STALL_MS:
                    health = "degraded"
                    health_error_code = "phase_stalled"
            if (
                monitoring_active
                and health == "healthy"
                and int(row["remaining_count"] or 0) > 0
                and progress_age_ms >= _MOBILE_PROGRESS_STALE_MS
            ):
                health = "degraded"
                health_error_code = "no_recent_progress"
            devices.append(
                {
                    "device_id": str(row["device_id"]),
                    "account_id": row["account_id"],
                    "health": health,
                    "reported_health": reported_health,
                    "health_error_code": health_error_code,
                    "health_updated_at_ms": row["updated_at_ms"],
                    "last_progress_at_ms": last_progress_at_ms,
                    "current_phase_started_at_ms": phase_started_at_ms,
                    "progress_age_ms": progress_age_ms,
                    "control_state": str(row["control_state"]),
                    "current_assignment": assignment,
                    "mean_ms": mean_ms,
                    "p50_ms": p50_ms,
                    "p90_ms": p90_ms,
                    "confirmed_visits_15m": confirmed_visits_15m,
                    "confirmed_visits_60m": confirmed_visits_60m,
                    "confirmed_rate_15m": float(confirmed_visits_15m * 4),
                    "confirmed_rate_60m": float(confirmed_visits_60m),
                    "stuck_assignments": stuck_by_device.get(str(row["device_id"]), 0),
                }
            )
        return devices

    def _browser_health_rows(
        self,
        connection: sqlite3.Connection,
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT account_id, page_role, device_id, status,
                   observed_at_ms, detail, observed_username,
                   last_scan_at_ms, last_success_at_ms, scan_state
            FROM browser_account_health
            ORDER BY account_id, page_role
            """
        ).fetchall()
        return merge_browser_health_rows(
            self.browser_accounts,
            [dict(row) for row in rows],
            now_ms=self.clock_ms(),
        )

    def _latest_diagnostics(
        self, connection: sqlite3.Connection, round_id: str
    ) -> dict[str, dict[str, object]]:
        rows = connection.execute(
            """
            WITH ranked AS (
                SELECT plan.device_id, attempt.result, attempt.diagnostics_json,
                       attempt.attempted_at_ms,
                       ROW_NUMBER() OVER (
                           PARTITION BY plan.device_id
                           ORDER BY attempt.attempted_at_ms DESC, attempt.attempt_id DESC
                       ) AS rank
                FROM action_attempts AS attempt
                JOIN device_action_plans AS plan ON plan.plan_id = attempt.plan_id
                WHERE plan.round_id = ?
            )
            SELECT device_id, result, diagnostics_json, attempted_at_ms
            FROM ranked WHERE rank = 1
            """,
            (round_id,),
        ).fetchall()
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            try:
                diagnostic = json.loads(str(row["diagnostics_json"]))
            except (TypeError, json.JSONDecodeError):
                diagnostic = {}
            screenshot_path = str(diagnostic.get("screenshot_path") or "")
            result[str(row["device_id"])] = {
                "result": str(row["result"]),
                "attempted_at_ms": int(row["attempted_at_ms"]),
                "ui_summary": str(diagnostic.get("ui_summary") or "")[:500],
                "screenshot_id": (
                    hashlib.sha256(screenshot_path.encode()).hexdigest()[:24]
                    if screenshot_path
                    else None
                ),
            }
        return result

    def _coverage_summary(
        self, connection: sqlite3.Connection, round_id: str
    ) -> dict[str, object]:
        required_devices = int(
            connection.execute(
                "SELECT COUNT(*) FROM round_device_seeds WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT identity_key,
                   SUM(visit_confirmed_at_ms IS NOT NULL) AS confirmed_devices,
                   SUM(phase = 'completed') AS completed_devices
            FROM round_assignments WHERE round_id = ? GROUP BY identity_key
            """,
            (round_id,),
        ).fetchall()
        targets = len(rows)
        confirmed_visits = sum(int(row["confirmed_devices"]) for row in rows)
        completed_assignments = sum(int(row["completed_devices"]) for row in rows)
        fully_covered = sum(
            int(row["confirmed_devices"]) == required_devices for row in rows
        )
        fully_completed = sum(
            int(row["completed_devices"]) == required_devices for row in rows
        )
        distribution: dict[str, int] = {}
        for row in rows:
            key = f"{int(row['confirmed_devices'])}/{required_devices}"
            distribution[key] = distribution.get(key, 0) + 1
        return {
            "targets": targets,
            "required_devices": required_devices,
            "confirmed_visits": confirmed_visits,
            "completed_assignments": completed_assignments,
            "fully_covered": fully_covered,
            "fully_completed": fully_completed,
            "distribution": distribution,
            "coverage_rate": 0.0 if targets == 0 else fully_covered / targets,
            "completion_rate": 0.0 if targets == 0 else fully_completed / targets,
        }

    def _quota_rows(
        self, connection: sqlite3.Connection, round_id: str
    ) -> list[dict[str, object]]:
        now_ms = self.clock_ms()
        window_start_ms = now_ms - 3_600_000
        windows = {
            (str(row["device_id"]), str(row["outcome"])): row
            for row in connection.execute(
                """
                SELECT device_id, effective_outcome AS outcome,
                       COUNT(*) AS reserved_count,
                       SUM(state = 'confirmed') AS confirmed_count,
                       SUM(state = 'uncertain') AS uncertain_count
                FROM device_action_plans
                WHERE effective_outcome <> 'trace'
                  AND created_at_ms > ? AND created_at_ms <= ?
                GROUP BY device_id, effective_outcome
                """,
                (window_start_ms, now_ms),
            )
        }
        pacing = {
            (str(row["device_id"]), str(row["outcome"])): row
            for row in connection.execute(
                """
                SELECT device_id, outcome, tokens, updated_at_ms
                FROM action_pacing_state
                """
            )
        }
        device_ids = [
            str(row["device_id"])
            for row in connection.execute(
                "SELECT device_id FROM round_device_seeds WHERE round_id = ? ORDER BY device_id",
                (round_id,),
            )
        ]
        results = []
        for device_id in device_ids:
            for outcome, limit in _QUOTA_LIMITS.items():
                row = windows.get((device_id, outcome))
                reserved = 0 if row is None else int(row["reserved_count"])
                confirmed = 0 if row is None else int(row["confirmed_count"])
                uncertain = 0 if row is None else int(row["uncertain_count"])
                pacing_row = pacing.get((device_id, outcome))
                if pacing_row is None:
                    digest = hashlib.sha256(f"{device_id}\0{outcome}".encode()).digest()
                    tokens = int.from_bytes(digest[:8], "big") / 2**64
                else:
                    elapsed_ms = max(0, now_ms - int(pacing_row["updated_at_ms"]))
                    tokens = min(
                        2.0,
                        float(pacing_row["tokens"]) + elapsed_ms * limit / 3_600_000,
                    )
                token_ready = tokens >= 1 and reserved < limit
                wait_ms = (
                    0 if tokens >= 1 else math.ceil((1 - tokens) * 3_600_000 / limit)
                )
                results.append(
                    {
                        "device_id": device_id,
                        "outcome": outcome,
                        "limit": limit,
                        "reserved": reserved,
                        "confirmed": confirmed,
                        "uncertain": uncertain,
                        "remaining": max(0, limit - reserved),
                        "rolling_window_started_at_ms": window_start_ms,
                        "token_ready": token_ready,
                        "next_due_at_ms": now_ms + wait_ms,
                        "candidate_weight": limit if token_ready else 0,
                    }
                )
        return results

    def _recent_traces(
        self, connection: sqlite3.Connection, round_id: str, *, limit: int
    ) -> list[dict[str, object]]:
        required_devices = int(
            connection.execute(
                "SELECT COUNT(*) FROM round_device_seeds WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT assignment.identity_key, target.username,
                   SUM(assignment.visit_confirmed_at_ms IS NOT NULL) AS confirmed_devices,
                   SUM(assignment.phase = 'completed') AS completed_devices,
                   MAX(assignment.visit_confirmed_at_ms) AS last_visit_confirmed_at_ms
            FROM round_assignments AS assignment
            JOIN exposure_rounds AS round ON round.round_id = assignment.round_id
            JOIN pool_targets AS target
              ON target.pool_id = round.pool_id
             AND target.identity_key = assignment.identity_key
            WHERE assignment.round_id = ?
            GROUP BY assignment.identity_key, target.username
            HAVING confirmed_devices > 0
            ORDER BY last_visit_confirmed_at_ms DESC, assignment.identity_key
            LIMIT ?
            """,
            (round_id, limit),
        ).fetchall()
        return [
            {
                "identity_key": str(row["identity_key"]),
                "username": str(row["username"]),
                "confirmed_devices": int(row["confirmed_devices"]),
                "completed_devices": int(row["completed_devices"]),
                "required_devices": required_devices,
                "fully_covered": int(row["confirmed_devices"]) == required_devices,
                "fully_completed": int(row["completed_devices"]) == required_devices,
                "last_visit_confirmed_at_ms": int(row["last_visit_confirmed_at_ms"]),
            }
            for row in rows
        ]
