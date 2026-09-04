import hashlib
import json
import os
import random
import sqlite3
import stat
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import yaml

from .acquisition_db import AcquisitionRepository
from .db import Database

DEMO_NAMESPACE = "demo-ai-growth-v1"
DEMO_POOL_ID = "demo-pool-ai-growth-v1"
DEMO_ROUND_ID = "demo-round-ai-growth-v1"
DEMO_POOL_IDENTITY_PREFIX = "demo:target:"
DEMO_ROUND_LABEL = "DEMO · AI 多账号获客转化试点"
DEMO_SEED = 20260904
DEMO_BACKUP_PROVENANCE_VERSION = 1
DEMO_HISTORY_ROUND_IDS = (
    "demo-round-ai-growth-history-01",
    "demo-round-ai-growth-history-02",
)
DEMO_HISTORY_POOL_IDS = (
    "demo-pool-ai-growth-history-01",
    "demo-pool-ai-growth-history-02",
)
_DAY_MS = 86_400_000
_SQL_BATCH_SIZE = 1_000
_INTERACTION_OUTCOMES = ("like", "favorite", "repost")


@dataclass(frozen=True)
class DemoScale:
    targets: int
    devices: int
    confirmed_visits: int
    fully_covered: int
    eligible: int
    interactions: int
    followers: int
    inbound: int
    engaged: int
    qualified: int
    invited: int
    contact_captured: int
    human_required: int
    sales: int
    ai_plans: int
    ai_sent: int
    ai_uncertain: int
    ai_superseded: int
    conversations: int
    manual_handled: int
    pending_inbound: int

    @property
    def assignments(self) -> int:
        return self.targets * self.devices

    @property
    def target_count(self) -> int:
        return self.targets

    @property
    def device_count(self) -> int:
        return self.devices

    @property
    def conversation_count(self) -> int:
        return self.conversations

    @classmethod
    def portfolio(cls) -> "DemoScale":
        return cls(
            targets=10_000,
            devices=7,
            confirmed_visits=68_420,
            fully_covered=9_770,
            eligible=5_860,
            interactions=4_410,
            followers=1_240,
            inbound=486,
            engaged=326,
            qualified=173,
            invited=126,
            contact_captured=72,
            human_required=28,
            sales=19,
            ai_plans=348,
            ai_sent=331,
            ai_uncertain=5,
            ai_superseded=12,
            conversations=20,
            manual_handled=98,
            pending_inbound=29,
        )

    @classmethod
    def test_fixture(cls) -> "DemoScale":
        return cls(
            targets=12,
            devices=3,
            confirmed_visits=31,
            fully_covered=10,
            eligible=8,
            interactions=6,
            followers=5,
            inbound=8,
            engaged=7,
            qualified=6,
            invited=5,
            contact_captured=4,
            human_required=1,
            sales=3,
            ai_plans=6,
            ai_sent=5,
            ai_uncertain=1,
            ai_superseded=0,
            conversations=8,
            manual_handled=2,
            pending_inbound=0,
        )


@dataclass(frozen=True)
class DemoMetrics:
    assignments: int
    confirmed_visits: int
    fully_covered: int
    eligible: int
    interactions: int
    followers: int
    inbound: int
    engaged: int
    qualified: int
    invited: int
    contact_captured: int
    human_required: int
    sales: int
    ai_plans: int
    ai_sent: int
    ai_uncertain: int
    ai_superseded: int


@dataclass(frozen=True)
class DemoAccount:
    account_id: str
    device_id: str
    profile_label: str
    username: str
    private_channel_hint: str


@dataclass(frozen=True)
class DemoConversation:
    lead_id: str
    conversation_key: str
    account_id: str
    language: str
    stage: str
    inbound_text: str
    outbound_text: str
    occurred_at_ms: int


@dataclass(frozen=True)
class DemoTimelineDay:
    date: str
    started_at_ms: int
    dm_inbound: int
    engaged: int
    qualified: int
    invited: int
    contact_captured: int
    human_required: int
    sales: int


@dataclass(frozen=True)
class DemoBlueprint:
    namespace: str
    pool_id: str
    round_id: str
    label: str
    now_ms: int
    scale: DemoScale
    targets: tuple[str, ...]
    accounts: tuple[DemoAccount, ...]
    conversations: tuple[DemoConversation, ...]
    timeline: tuple[DemoTimelineDay, ...]
    metrics: DemoMetrics


@dataclass(frozen=True)
class DemoSeedResult:
    created: Mapping[str, int]
    summary: Mapping[str, int]
    backup_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "created", MappingProxyType(dict(self.created)))
        object.__setattr__(self, "summary", MappingProxyType(dict(self.summary)))

    @property
    def created_total(self) -> int:
        return sum(self.created.values())


def build_demo_blueprint(
    *,
    now_ms: int,
    scale: DemoScale | None = None,
) -> DemoBlueprint:
    selected = scale or DemoScale.portfolio()
    if now_ms <= 0:
        raise ValueError("demo clock must be positive")
    return _build_blueprint(selected, now_ms=now_ms, seed=DEMO_SEED)


def seed_demo_database(
    path: Path,
    blueprint: DemoBlueprint,
    *,
    web_accounts_path: Path | None = None,
    runtime_settings_path: Path | None = None,
    backup_dir: Path | None = None,
) -> DemoSeedResult:
    """Persist one deterministic demo dataset without activating any worker."""
    database_path = Path(path)
    configuration_paths = (web_accounts_path, runtime_settings_path)
    if any(item is not None for item in configuration_paths):
        if not all(item is not None for item in configuration_paths):
            raise ValueError("both demo configuration paths are required")
        if backup_dir is None:
            raise ValueError("backup directory is required for configuration output")

    staged_files: dict[Path, Path] = {}
    prior_files: dict[Path, bytes | None] = {}
    backup_path: Path | None = None
    backup_reused = False

    if web_accounts_path is not None and runtime_settings_path is not None:
        destinations = (Path(web_accounts_path), Path(runtime_settings_path))
        prior_files = {
            destination: destination.read_bytes() if destination.exists() else None
            for destination in destinations
        }
        expected_backup_path = _database_backup_path(Path(backup_dir), blueprint.now_ms)
        try:
            expected_backup_path.lstat()
        except FileNotFoundError:
            pass
        else:
            backup_reused = True
        backup_path = create_database_backup(
            database_path,
            Path(backup_dir),
            blueprint.now_ms,
            replay_blueprint=blueprint,
        )
        try:
            staged_files = _stage_configuration_files(
                blueprint,
                web_accounts_path=destinations[0],
                runtime_settings_path=destinations[1],
            )
        except BaseException:
            for staged in staged_files.values():
                staged.unlink(missing_ok=True)
            raise

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        AcquisitionRepository(database_path).migrate(connection=connection)
        Database(database_path).migrate(connection=connection)
        created = _seed_acquisition(connection, blueprint)
        conversion_created = _seed_conversion(connection, blueprint)
        for name, count in conversion_created.items():
            created[name] = created.get(name, 0) + count
        summary = _seed_summary(connection, blueprint)
        if backup_reused and sum(created.values()):
            raise FileExistsError(
                "existing demo backup cannot repair an incomplete replay"
            )
        if backup_reused and any(
            prior_files[destination] != staged.read_bytes()
            for destination, staged in staged_files.items()
        ):
            raise FileExistsError(
                "existing demo backup requires exact replay configuration"
            )
        for destination, staged in staged_files.items():
            os.replace(staged, destination)
            os.chmod(destination, 0o600)
        connection.commit()
    except BaseException:
        connection.rollback()
        connection.close()
        for destination, previous in prior_files.items():
            _restore_file(destination, previous)
        raise
    finally:
        try:
            connection.close()
        finally:
            for staged in staged_files.values():
                staged.unlink(missing_ok=True)
    return DemoSeedResult(
        created=created,
        summary=summary,
        backup_path=backup_path,
    )


def create_database_backup(
    path: Path,
    backup_dir: Path,
    now_ms: int,
    *,
    replay_blueprint: DemoBlueprint | None = None,
) -> Path:
    """Create a consistent SQLite backup before compensated demo seeding."""
    source_path = Path(path)
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_status = destination_dir.lstat()
    if not stat.S_ISDIR(directory_status.st_mode):
        raise ValueError("demo backup destination must be a directory")
    os.chmod(destination_dir, 0o700)
    destination = _database_backup_path(destination_dir, now_ms)
    provenance_path = destination.with_suffix(".json")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        _validate_existing_database_backup(destination)
        _validate_backup_provenance(
            destination,
            provenance_path,
            namespace=(
                replay_blueprint.namespace
                if replay_blueprint is not None
                else DEMO_NAMESPACE
            ),
            now_ms=now_ms,
        )
        if replay_blueprint is None or not _is_complete_demo_replay(
            source_path, replay_blueprint
        ):
            raise FileExistsError(
                "existing demo backup is reusable only for a complete idempotent replay"
            )
        return destination
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination_dir,
    )
    temporary = Path(temporary_name)
    provenance_temporary: Path | None = None
    try:
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        if source_path.exists():
            source_status = source_path.lstat()
            if not stat.S_ISREG(source_status.st_mode):
                raise ValueError("demo database source must be a regular file")
            with (
                _open_database_read_only(source_path) as source,
                sqlite3.connect(temporary) as target,
            ):
                source.backup(target)
        else:
            with sqlite3.connect(temporary) as target:
                target.execute("CREATE TABLE demo_empty_backup_marker(value INTEGER)")
                target.execute("DROP TABLE demo_empty_backup_marker")
        os.chmod(temporary, 0o600)
        _validate_existing_database_backup(temporary)
        provenance = _backup_provenance(
            temporary,
            backup_filename=destination.name,
            namespace=(
                replay_blueprint.namespace
                if replay_blueprint is not None
                else DEMO_NAMESPACE
            ),
            now_ms=now_ms,
        )
        provenance_temporary = _stage_bytes(
            provenance_path,
            json.dumps(
                provenance,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        os.link(temporary, destination)
        try:
            os.link(provenance_temporary, provenance_path)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(f"{temporary}{suffix}").unlink(missing_ok=True)
        if provenance_temporary is not None:
            provenance_temporary.unlink(missing_ok=True)
    return destination


def _database_backup_path(backup_dir: Path, now_ms: int) -> Path:
    timestamp = datetime.fromtimestamp(int(now_ms) / 1_000, UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    return Path(backup_dir) / f"tikpoc-before-demo-{timestamp}.db"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_provenance(
    path: Path, *, backup_filename: str, namespace: str, now_ms: int
) -> dict[str, object]:
    return {
        "schema_version": DEMO_BACKUP_PROVENANCE_VERSION,
        "namespace": namespace,
        "now_ms": int(now_ms),
        "backup_filename": backup_filename,
        "sha256": _file_sha256(path),
    }


def _validate_backup_provenance(
    backup_path: Path,
    provenance_path: Path,
    *,
    namespace: str,
    now_ms: int,
) -> None:
    try:
        status = provenance_path.lstat()
    except FileNotFoundError as error:
        raise FileExistsError("demo backup provenance sidecar is missing") from error
    if not stat.S_ISREG(status.st_mode):
        raise FileExistsError("demo backup provenance must be a regular file")
    if stat.S_IMODE(status.st_mode) != 0o600:
        raise PermissionError("demo backup provenance has insecure permissions")
    try:
        document = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("demo backup provenance is invalid") from error
    expected = _backup_provenance(
        backup_path,
        backup_filename=backup_path.name,
        namespace=namespace,
        now_ms=now_ms,
    )
    if document != expected:
        if isinstance(document, dict) and document.get("sha256") != expected["sha256"]:
            raise ValueError("demo backup provenance digest mismatch")
        raise ValueError("demo backup provenance metadata mismatch")


def _open_database_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _validate_existing_database_backup(path: Path) -> None:
    status = path.lstat()
    if not stat.S_ISREG(status.st_mode):
        raise FileExistsError("demo backup collision is not a regular file")
    if stat.S_IMODE(status.st_mode) != 0o600:
        raise PermissionError("demo backup collision has insecure permissions")
    try:
        with _open_database_read_only(path) as connection:
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("demo backup collision failed SQLite quick_check")
            has_rounds = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='exposure_rounds'"
            ).fetchone()
            if (
                has_rounds
                and connection.execute(
                    "SELECT 1 FROM exposure_rounds WHERE round_id=? LIMIT 1",
                    (DEMO_ROUND_ID,),
                ).fetchone()
            ):
                raise ValueError("demo backup collision contains seeded demo data")
    except sqlite3.DatabaseError as error:
        raise ValueError(
            "demo backup collision is not a valid SQLite database"
        ) from error


def _is_complete_demo_replay(path: Path, blueprint: DemoBlueprint) -> bool:
    try:
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode):
            return False
        with _open_database_read_only(path) as connection:
            required_tables = {
                "target_pools",
                "pool_targets",
                "exposure_rounds",
                "round_assignments",
                "browser_reply_plans",
            }
            present_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not required_tables <= present_tables:
                return False
            checksum = hashlib.sha256(
                f"{blueprint.namespace}\0{len(blueprint.targets)}".encode()
            ).hexdigest()
            pool_matches = connection.execute(
                """
                SELECT COUNT(*) FROM target_pools
                WHERE pool_id=? AND source_name=? AND source_checksum=?
                  AND unique_targets=? AND source_rows=?
                """,
                (
                    blueprint.pool_id,
                    blueprint.label,
                    checksum,
                    blueprint.scale.targets,
                    blueprint.scale.targets,
                ),
            ).fetchone()[0]
            round_matches = connection.execute(
                "SELECT COUNT(*) FROM exposure_rounds WHERE round_id=? AND pool_id=?",
                (blueprint.round_id, blueprint.pool_id),
            ).fetchone()[0]
            target_count = connection.execute(
                "SELECT COUNT(*) FROM pool_targets WHERE pool_id=?",
                (blueprint.pool_id,),
            ).fetchone()[0]
            assignment_count = connection.execute(
                "SELECT COUNT(*) FROM round_assignments WHERE round_id=?",
                (blueprint.round_id,),
            ).fetchone()[0]
            placeholders = ",".join("?" for _ in blueprint.accounts)
            ai_plan_count = connection.execute(
                f"SELECT COUNT(*) FROM browser_reply_plans "
                f"WHERE plan_origin='ai' AND account_id IN ({placeholders})",
                tuple(account.account_id for account in blueprint.accounts),
            ).fetchone()[0]
            return (
                int(pool_matches) == 1
                and int(round_matches) == 1
                and int(target_count) == blueprint.scale.targets
                and int(assignment_count) == blueprint.scale.assignments
                and int(ai_plan_count) == blueprint.metrics.ai_plans
            )
    except (FileNotFoundError, sqlite3.DatabaseError, OSError):
        return False


def clear_demo_database(
    path: Path,
    *,
    web_accounts_path: Path | None = None,
    runtime_settings_path: Path | None = None,
) -> DemoSeedResult:
    """Remove only entities owned by the fixed synthetic namespace."""
    database_path = Path(path)
    AcquisitionRepository(database_path).migrate()
    Database(database_path).migrate()
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        deleted: dict[str, int] = {}
        statements = (
            (
                "action_attempts",
                (
                    "DELETE FROM action_attempts WHERE plan_id IN "
                    "(SELECT plan_id FROM device_action_plans "
                    "WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02'))"
                ),
            ),
            (
                "assignment_phase_history",
                (
                    "DELETE FROM assignment_phase_history WHERE assignment_id IN "
                    "(SELECT assignment_id FROM round_assignments "
                    "WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02'))"
                ),
            ),
            (
                "assignment_stage_timings",
                (
                    "DELETE FROM assignment_stage_timings WHERE assignment_id IN "
                    "(SELECT assignment_id FROM round_assignments "
                    "WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02'))"
                ),
            ),
            (
                "assignment_command_metrics",
                (
                    "DELETE FROM assignment_command_metrics WHERE assignment_id IN "
                    "(SELECT assignment_id FROM round_assignments "
                    "WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02'))"
                ),
            ),
            (
                "acquisition_quota_windows",
                "DELETE FROM acquisition_quota_windows WHERE device_id LIKE 'demo-device-%'",
            ),
            (
                "device_action_plans",
                "DELETE FROM device_action_plans WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02')",
            ),
            (
                "profile_snapshot_leases",
                "DELETE FROM profile_snapshot_leases WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02')",
            ),
            (
                "profile_snapshots",
                "DELETE FROM profile_snapshots WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02')",
            ),
            (
                "round_assignments",
                "DELETE FROM round_assignments WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02')",
            ),
            (
                "round_device_seeds",
                "DELETE FROM round_device_seeds WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02')",
            ),
            (
                "exposure_rounds",
                "DELETE FROM exposure_rounds WHERE round_id IN ('demo-round-ai-growth-v1', 'demo-round-ai-growth-history-01', 'demo-round-ai-growth-history-02')",
            ),
            (
                "pool_targets",
                (
                    "DELETE FROM pool_targets "
                    "WHERE pool_id IN ('demo-pool-ai-growth-v1', 'demo-pool-ai-growth-history-01', 'demo-pool-ai-growth-history-02') "
                    "OR identity_key LIKE 'demo:target:%'"
                ),
            ),
            (
                "target_pools",
                "DELETE FROM target_pools WHERE pool_id IN ('demo-pool-ai-growth-v1', 'demo-pool-ai-growth-history-01', 'demo-pool-ai-growth-history-02')",
            ),
            (
                "fleet_device_health",
                "DELETE FROM fleet_device_health WHERE device_id LIKE 'demo-device-%'",
            ),
            (
                "operator_control_states",
                "DELETE FROM operator_control_states WHERE scope_id LIKE 'demo-device-%'",
            ),
            (
                "operator_lead_commands",
                "DELETE FROM operator_lead_commands WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "browser_action_leases",
                "DELETE FROM browser_action_leases WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "browser_action_circuits",
                "DELETE FROM browser_action_circuits WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "browser_contact_suppressions",
                "DELETE FROM browser_contact_suppressions WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "browser_reply_plans",
                (
                    "DELETE FROM browser_reply_plans "
                    "WHERE account_id LIKE 'demo-account-%' "
                    "OR inbound_fingerprint LIKE 'demo:%'"
                ),
            ),
            (
                "browser_welcome_plans",
                "DELETE FROM browser_welcome_plans WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "web_messages",
                (
                    "DELETE FROM web_messages "
                    "WHERE account_id LIKE 'demo-account-%' "
                    "OR message_id LIKE 'demo:%'"
                ),
            ),
            (
                "lead_funnel_events",
                (
                    "DELETE FROM lead_funnel_events "
                    "WHERE account_id LIKE 'demo-account-%' "
                    "OR participant_username GLOB 'demo_lead_*' "
                    "OR source_key LIKE 'demo:%'"
                ),
            ),
            (
                "lead_sales",
                (
                    "DELETE FROM lead_sales "
                    "WHERE account_id LIKE 'demo-account-%' "
                    "OR participant_username GLOB 'demo_lead_*'"
                ),
            ),
            (
                "browser_account_health",
                "DELETE FROM browser_account_health WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "web_account_settings",
                "DELETE FROM web_account_settings WHERE account_id LIKE 'demo-account-%'",
            ),
            (
                "web_events",
                (
                    "DELETE FROM web_events "
                    "WHERE account_id LIKE 'demo-account-%' "
                    "OR dedup_key LIKE 'demo:%'"
                ),
            ),
            (
                "web_conversations",
                (
                    "DELETE FROM web_conversations "
                    "WHERE account_id LIKE 'demo-account-%' "
                    "OR conversation_id LIKE 'demo:%' "
                    "OR participant_username GLOB 'demo_lead_*'"
                ),
            ),
        )
        for name, sql in statements:
            deleted[name] = _execute_counted(connection, sql, ())
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    if web_accounts_path is not None:
        _remove_demo_web_accounts(Path(web_accounts_path))
    if runtime_settings_path is not None:
        _remove_demo_runtime_accounts(Path(runtime_settings_path))
    return DemoSeedResult(created=deleted, summary={})


def _stage_configuration_files(
    blueprint: DemoBlueprint,
    *,
    web_accounts_path: Path,
    runtime_settings_path: Path,
) -> dict[Path, Path]:
    accounts_document = {
        "accounts": [
            {
                "account_id": account.account_id,
                "device_id": account.device_id,
                "mode": "browser",
                "expected_tiktok_username": account.username,
                "browser_profile_label": account.profile_label,
                "private_channel_hint": account.private_channel_hint,
                "offer_context": "DEMO catalog: synthetic portfolio product context.",
                "faq_file": "",
                "faq_context": "DEMO FAQ: availability and delivery are illustrative.",
                "reply_language": "auto",
                "max_auto_replies": 12,
                "invite_after_meaningful_turns": 2,
                "fallback_acknowledgement": "Thanks for your DEMO message.",
                "browser_followback_enabled": False,
                "browser_dm_enabled": False,
                "enabled": False,
            }
            for account in blueprint.accounts
        ]
    }
    existing_settings: dict[str, object] = {}
    if runtime_settings_path.exists():
        loaded = json.loads(runtime_settings_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("runtime settings must be an object")
        existing_settings = loaded
    existing_accounts = existing_settings.get("accounts", {})
    if not isinstance(existing_accounts, dict):
        raise TypeError("runtime account settings must be an object")
    runtime_accounts = {
        key: value
        for key, value in existing_accounts.items()
        if not str(key).startswith("demo-account-")
    }
    runtime_accounts.update(
        {
            account.account_id: {
                "whatsapp": "",
                "telegram": account.private_channel_hint,
                "offer_context": "DEMO catalog: synthetic portfolio product context.",
                "faq_context": "DEMO FAQ: illustrative availability and delivery facts.",
                "reply_tone": "Concise, helpful, bilingual demo tone.",
                "brand_name": "TikPoc DEMO",
                "welcome_after_followback": False,
                "welcome_language": "English",
            }
            for account in blueprint.accounts
        }
    )
    settings_document = dict(existing_settings)
    settings_document.setdefault(
        "provider", {"base_url": "", "api_key": "", "model": ""}
    )
    provider = settings_document["provider"]
    if not isinstance(provider, dict):
        provider = {}
    settings_document["provider"] = {
        "base_url": str(provider.get("base_url") or ""),
        "api_key": "",
        "model": str(provider.get("model") or ""),
    }
    settings_document["accounts"] = runtime_accounts
    staged: dict[Path, Path] = {}
    try:
        staged[web_accounts_path] = _stage_bytes(
            web_accounts_path,
            yaml.safe_dump(
                accounts_document,
                allow_unicode=True,
                sort_keys=False,
            ).encode("utf-8"),
        )
        staged[runtime_settings_path] = _stage_bytes(
            runtime_settings_path,
            json.dumps(
                settings_document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    except BaseException:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        raise
    return staged


def _stage_bytes(destination: Path, content: bytes) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.demo.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def _restore_database_backup(path: Path, backup_path: Path) -> None:
    temporary = path.with_name(f".{path.name}.restore.tmp")
    with sqlite3.connect(backup_path) as source, sqlite3.connect(temporary) as target:
        source.backup(target)
    path.with_name(f"{path.name}-wal").unlink(missing_ok=True)
    path.with_name(f"{path.name}-shm").unlink(missing_ok=True)
    os.replace(temporary, path)


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    staged = _stage_bytes(path, content)
    os.replace(staged, path)


def _remove_demo_web_accounts(path: Path) -> None:
    if not path.exists():
        return
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    accounts = document.get("accounts") if isinstance(document, dict) else None
    if (
        isinstance(accounts, list)
        and accounts
        and all(
            isinstance(item, dict)
            and str(item.get("account_id", "")).startswith("demo-account-")
            for item in accounts
        )
    ):
        path.unlink()


def _remove_demo_runtime_accounts(path: Path) -> None:
    if not path.exists():
        return
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("runtime settings must be an object")
    accounts = document.get("accounts", {})
    if not isinstance(accounts, dict):
        raise TypeError("runtime account settings must be an object")
    document["accounts"] = {
        key: value
        for key, value in accounts.items()
        if not str(key).startswith("demo-account-")
    }
    staged = _stage_bytes(
        path,
        json.dumps(document, ensure_ascii=True, sort_keys=True).encode("utf-8"),
    )
    os.replace(staged, path)


def _seed_summary(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT SUM(effective_outcome <> 'trace') AS interactions,
               SUM(effective_outcome = 'trace') AS traces,
               SUM(state = 'uncertain') AS uncertain
        FROM device_action_plans WHERE round_id = ?
        """,
        (blueprint.round_id,),
    ).fetchone()
    return {
        "interaction_plans": int(row["interactions"] or 0),
        "trace_plans": int(row["traces"] or 0),
        "uncertain_action_plans": int(row["uncertain"] or 0),
    }


def _seed_acquisition(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> dict[str, int]:
    created: dict[str, int] = {}
    created["worker_controls"] = _execute_counted(
        connection,
        """
        UPDATE worker_control SET requested_state = 'stopped'
        WHERE singleton = 1 AND requested_state <> 'stopped'
        """,
        (),
    )
    checksum = hashlib.sha256(
        f"{blueprint.namespace}\0{len(blueprint.targets)}".encode()
    ).hexdigest()
    created["pools"] = _execute_counted(
        connection,
        """
        INSERT OR IGNORE INTO target_pools(
            pool_id, source_name, source_checksum,
            unique_targets, source_rows, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            blueprint.pool_id,
            blueprint.label,
            checksum,
            len(blueprint.targets),
            len(blueprint.targets),
            _demo_started_at_ms(blueprint),
        ),
    )

    target_rows = (
        (
            blueprint.pool_id,
            _target_identity(index),
            target_id,
            f"demo-sec-uid-{index:05d}",
            target_id,
            f"https://example.invalid/demo-profile/{index:05d}",
            f"demo-video-{index:05d}",
            json.dumps([index], separators=(",", ":")),
            index - 1,
        )
        for index, target_id in enumerate(blueprint.targets, start=1)
    )
    created["targets"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO pool_targets(
            pool_id, identity_key, target_id, sec_uid, username,
            profile_url, source_video_id, source_line_numbers_json, ordinal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        target_rows,
    )
    history_pools = tuple(
        (
            pool_id,
            f"{blueprint.label} · 历史 {index:02d}",
            hashlib.sha256(
                f"{blueprint.namespace}\0history\0{index}".encode()
            ).hexdigest(),
            1,
            1,
            max(1, _demo_started_at_ms(blueprint) - index * 14 * _DAY_MS),
        )
        for index, pool_id in enumerate(DEMO_HISTORY_POOL_IDS, start=1)
    )
    created["historical_pools"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO target_pools(
            pool_id, source_name, source_checksum,
            unique_targets, source_rows, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        history_pools,
    )
    created["historical_targets"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO pool_targets(
            pool_id, identity_key, target_id, sec_uid, username,
            profile_url, source_video_id, source_line_numbers_json, ordinal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '[1]', 0)
        """,
        (
            (
                pool_id,
                _history_identity(index),
                _history_username(index),
                f"demo-history-sec-uid-{index:02d}",
                _history_username(index),
                f"https://example.invalid/demo-history-profile/{index:02d}",
                f"demo-history-video-{index:02d}",
            )
            for index, pool_id in enumerate(DEMO_HISTORY_POOL_IDS, start=1)
        ),
    )
    created["rounds"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO exposure_rounds(
            round_id, pool_id, state, starts_at_ms,
            min_inter_device_gap_ms, min_repeat_gap_ms, created_at_ms,
            navigation_mode
        ) VALUES (?, ?, ?, ?, 0, 0, ?, 'deeplink')
        """,
        (
            (
                round_id,
                _round_pool_id(blueprint, round_id),
                state,
                starts_at_ms,
                starts_at_ms,
            )
            for round_id, state, starts_at_ms in _demo_round_rows(blueprint)
        ),
    )
    created["device_seeds"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO round_device_seeds(round_id, device_id, order_seed)
        VALUES (?, ?, ?)
        """,
        (
            (
                round_id,
                account.device_id,
                f"{blueprint.namespace}:order:{round_id}:{account.device_id}",
            )
            for round_id, _state, _started_at_ms in _demo_round_rows(blueprint)
            for account in blueprint.accounts
        ),
    )
    created["assignments"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO round_assignments(
            round_id, identity_key, device_id, order_key
        ) VALUES (?, ?, ?, ?)
        """,
        (
            (
                blueprint.round_id,
                _target_identity(target_index),
                account.device_id,
                _order_key(
                    blueprint.namespace,
                    target_index=target_index,
                    device_id=account.device_id,
                ),
            )
            for target_index in range(1, len(blueprint.targets) + 1)
            for account in blueprint.accounts
        ),
    )

    confirmed_keys = _confirmed_assignment_keys(blueprint)
    uncertain_keys = _uncertain_assignment_keys(blueprint, confirmed_keys)
    assignment_updates = (
        (
            "deferred" if (identity_key, device_id) in uncertain_keys else "completed",
            1,
            _assignment_base_timestamp(blueprint, identity_key) + 400,
            (
                None
                if (identity_key, device_id) in uncertain_keys
                else _assignment_completed_timestamp(blueprint, identity_key, sequence)
            ),
            blueprint.round_id,
            identity_key,
            device_id,
        )
        for sequence, (identity_key, device_id) in enumerate(confirmed_keys)
    )
    created["confirmed_visits"] = _executemany_bounded(
        connection,
        """
        UPDATE round_assignments
        SET phase=?, attempt_count=?, visit_confirmed_at_ms=?, completed_at_ms=?,
            last_error_code=NULL
        WHERE round_id=? AND identity_key=? AND device_id=?
          AND visit_confirmed_at_ms IS NULL
        """,
        assignment_updates,
    )
    created["phase_history"] = _insert_phase_history(
        connection, blueprint, confirmed_keys
    )
    created["profile_snapshots"] = _insert_profile_snapshots(
        connection, blueprint, confirmed_keys
    )
    action_counts = _insert_action_evidence(
        connection, blueprint, confirmed_keys, uncertain_keys
    )
    created.update(action_counts)
    created.update(_seed_historical_evidence(connection, blueprint))
    created["device_health"] = _insert_device_health(connection, blueprint)
    created["operator_controls"] = _insert_operator_controls(connection, blueprint)
    return created


def _seed_conversion(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> dict[str, int]:
    """Seed domain-shaped browser and funnel rows in the active transaction."""
    created: dict[str, int] = {}
    detailed = {
        index: item for index, item in enumerate(blueprint.conversations, start=1)
    }
    ai_states, manual_indexes, human_indexes = _conversion_decisions(
        blueprint, detailed
    )
    inbound_count = blueprint.metrics.inbound
    follower_count = blueprint.metrics.followers

    created["web_conversations"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO web_conversations(
            account_id, conversation_id, participant_id, participant_username,
            is_follower, stage, meaningful_turns, auto_reply_count,
            last_invited_at_ms, contact_captured_at_ms, human_required
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                f"demo:participant:{index:04d}",
                _lead_id(blueprint, index),
                int(index <= follower_count),
                _conversation_stage(blueprint, index, detailed, human_indexes),
                int(index <= blueprint.metrics.engaged),
                int(ai_states.get(index) == "sent"),
                (
                    _conversion_timestamp(blueprint, index)
                    if _conversation_stage(blueprint, index, detailed, human_indexes)
                    in {"invited", "contact_captured", "closed"}
                    else 0
                ),
                (
                    _conversion_timestamp(blueprint, index)
                    if _conversation_stage(blueprint, index, detailed, human_indexes)
                    in {"contact_captured", "closed"}
                    else 0
                ),
                int(index in human_indexes),
            )
            for index in range(1, max(inbound_count, follower_count) + 1)
        ),
    )
    created["inbound_messages"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO web_messages(
            account_id, conversation_id, message_id, direction,
            message_type, text, timestamp_ms
        ) VALUES (?, ?, ?, 'inbound', 'TEXT', ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                _inbound_fingerprint(index),
                (
                    detailed[index].inbound_text
                    if index in detailed
                    else _aggregate_inbound_text(index)
                ),
                _conversion_timestamp(blueprint, index),
            )
            for index in range(1, inbound_count + 1)
        ),
    )
    representative_sent = tuple(
        index for index in (1, 4) if index in ai_states and ai_states[index] == "sent"
    )
    created["history_messages"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO web_messages(
            account_id, conversation_id, message_id, direction,
            message_type, text, timestamp_ms
        ) VALUES (?, ?, ?, 'outbound', 'TEXT', ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                f"demo:welcome:{index:04d}",
                (
                    "您好，欢迎了解演示商品。"
                    if detailed[index].language == "zh"
                    else "Welcome—happy to help with the DEMO catalog."
                ),
                max(1, _conversion_timestamp(blueprint, index) - 15_000),
            )
            for index in representative_sent
        ),
    )

    created["ai_reply_plans"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO browser_reply_plans(
            account_id, conversation_id, inbound_fingerprint,
            participant_username, inbound_text, inbound_timestamp_ms,
            reply_text, stage, state, plan_origin,
            source_inbound_fingerprint, invitation_included,
            invitation_evidence_known, created_at_ms, sent_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai', ?, ?, 1, ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                _inbound_fingerprint(index),
                _lead_id(blueprint, index),
                (
                    detailed[index].inbound_text
                    if index in detailed
                    else _aggregate_inbound_text(index)
                ),
                _conversion_timestamp(blueprint, index),
                (
                    detailed[index].outbound_text
                    if index in detailed
                    else _aggregate_outbound_text(index)
                ),
                _conversation_stage(blueprint, index, detailed, human_indexes),
                ai_states[index],
                _inbound_fingerprint(index),
                int(
                    _conversation_stage(blueprint, index, detailed, human_indexes)
                    in {"invited", "contact_captured", "closed"}
                ),
                _conversion_timestamp(blueprint, index) + 100,
                (
                    _conversion_timestamp(blueprint, index) + 38_100
                    if ai_states[index] == "sent"
                    else 0
                ),
            )
            for index in ai_states
        ),
    )
    created["manual_reply_plans"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO browser_reply_plans(
            account_id, conversation_id, inbound_fingerprint,
            participant_username, inbound_text, inbound_timestamp_ms,
            reply_text, stage, state, plan_origin,
            source_inbound_fingerprint, invitation_evidence_known,
            created_at_ms, sent_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent', 'manual', ?, 1, ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                f"demo:manual-plan:{index:04d}",
                _lead_id(blueprint, index),
                _aggregate_inbound_text(index),
                _conversion_timestamp(blueprint, index),
                "DEMO manual reply / 演示人工回复。",
                _conversation_stage(blueprint, index, detailed, human_indexes),
                _inbound_fingerprint(index),
                0,
                _conversion_timestamp(blueprint, index) + 600,
            )
            for index in manual_indexes
        ),
    )
    created["outbound_messages"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO web_messages(
            account_id, conversation_id, message_id, direction, message_type,
            text, timestamp_ms, in_reply_to_message_id
        ) VALUES (?, ?, ?, 'outbound', 'TEXT', ?, ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                f"demo:outbound:{index:04d}",
                (
                    detailed[index].outbound_text
                    if index in detailed
                    else _aggregate_outbound_text(index)
                ),
                _conversion_timestamp(blueprint, index) + 38_100,
                _inbound_fingerprint(index),
            )
            for index, state in ai_states.items()
            if state == "sent"
        ),
    )
    created["outbound_messages"] += _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO web_messages(
            account_id, conversation_id, message_id, direction, message_type,
            text, timestamp_ms, in_reply_to_message_id
        ) VALUES (?, ?, ?, 'outbound', 'TEXT', ?, ?, ?)
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _conversation_key(blueprint, index),
                f"demo:manual-outbound:{index:04d}",
                "DEMO manual reply / 演示人工回复。",
                _conversion_timestamp(blueprint, index) + 600,
                _inbound_fingerprint(index),
            )
            for index in manual_indexes
        ),
    )

    for stage in (
        "dm_inbound",
        "engaged",
        "qualified",
        "invited",
        "contact_captured",
        "human_required",
    ):
        total = int(
            blueprint.metrics.inbound
            if stage == "dm_inbound"
            else getattr(blueprint.metrics, stage)
        )
        created[f"funnel_{stage}"] = _executemany_bounded(
            connection,
            """
            INSERT OR IGNORE INTO lead_funnel_events(
                account_id, participant_username, conversation_id,
                stage, source_key, occurred_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    _conversion_account(blueprint, index).account_id,
                    _lead_id(blueprint, index),
                    _conversation_key(blueprint, index),
                    stage,
                    f"demo:funnel:{stage}:{index:04d}",
                    _conversion_timestamp(blueprint, index, total=total),
                )
                for index in range(1, total + 1)
            ),
        )

    created["sales"] = _executemany_bounded(
        connection,
        """
        INSERT INTO lead_sales(
            account_id, participant_username, amount_minor,
            currency, status, occurred_at_ms
        )
        SELECT ?, ?, ?, 'USD', 'confirmed', ?
        WHERE NOT EXISTS (
            SELECT 1 FROM lead_sales
            WHERE account_id=? AND participant_username=?
              AND status='confirmed' AND occurred_at_ms=?
        )
        """,
        (
            (
                _conversion_account(blueprint, index).account_id,
                _lead_id(blueprint, index),
                12_900 + index * 100,
                _conversion_timestamp(blueprint, index, total=blueprint.metrics.sales)
                + 900,
                _conversion_account(blueprint, index).account_id,
                _lead_id(blueprint, index),
                _conversion_timestamp(blueprint, index, total=blueprint.metrics.sales)
                + 900,
            )
            for index in range(1, blueprint.metrics.sales + 1)
        ),
    )
    created["browser_health"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO browser_account_health(
            account_id, page_role, device_id, status, observed_at_ms, detail,
            observed_username, last_scan_at_ms, last_success_at_ms, scan_state
        ) VALUES (?, ?, ?, 'ready', ?, ?, ?, ?, ?, 'idle')
        """,
        (
            (
                account.account_id,
                page_role,
                account.device_id,
                blueprint.now_ms,
                "DEMO synthetic browser observer; execution disabled",
                account.username,
                blueprint.now_ms,
                blueprint.now_ms,
            )
            for account in blueprint.accounts
            for page_role in ("activity", "messages")
        ),
    )
    created["web_account_settings"] = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO web_account_settings(
            account_id, ai_enabled, followback_enabled
        ) VALUES (?, 0, 0)
        """,
        ((account.account_id,) for account in blueprint.accounts),
    )
    return created


def _insert_phase_history(
    connection: sqlite3.Connection,
    blueprint: DemoBlueprint,
    confirmed_keys: Sequence[tuple[str, str]],
) -> int:
    rows = (
        (
            "pending",
            "profile_opening",
            json.dumps({"source": blueprint.namespace}, separators=(",", ":")),
            _assignment_base_timestamp(blueprint, identity_key),
            blueprint.round_id,
            identity_key,
            device_id,
        )
        for identity_key, device_id in confirmed_keys
    )
    return _executemany_bounded(
        connection,
        """
        INSERT INTO assignment_phase_history(
            assignment_id, from_phase, to_phase, details_json, changed_at_ms
        )
        SELECT assignment.assignment_id, ?, ?, ?, ?
        FROM round_assignments AS assignment
        WHERE assignment.round_id = ?
          AND assignment.identity_key = ?
          AND assignment.device_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM assignment_phase_history AS history
              WHERE history.assignment_id = assignment.assignment_id
                AND history.to_phase = 'profile_opening'
          )
        """,
        rows,
    )


def _insert_profile_snapshots(
    connection: sqlite3.Connection,
    blueprint: DemoBlueprint,
    confirmed_keys: Sequence[tuple[str, str]],
) -> int:
    if not blueprint.accounts:
        return 0
    confirmed_identities = tuple(dict.fromkeys(key[0] for key in confirmed_keys))
    rows = (
        (
            blueprint.round_id,
            identity_key,
            blueprint.accounts[0].device_id,
            blueprint.targets[target_index - 1],
            420 + index % 180,
            1_300 + index % 700,
            3 + index % 9 if target_index <= blueprint.metrics.eligible else 0,
            int(target_index <= blueprint.metrics.eligible),
            "eligible"
            if target_index <= blueprint.metrics.eligible
            else "insufficient_posts",
            _assignment_base_timestamp(blueprint, identity_key) + 500,
        )
        for index, identity_key in enumerate(confirmed_identities, start=1)
        for target_index in (_target_index(identity_key),)
    )
    return _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO profile_snapshots(
            round_id, identity_key, observed_by_device_id, observed_username,
            following_count, followers_count, post_count, private_account,
            access_state, eligible, reason, observed_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'public', ?, ?, ?)
        """,
        rows,
    )


def _insert_action_evidence(
    connection: sqlite3.Connection,
    blueprint: DemoBlueprint,
    confirmed_keys: Sequence[tuple[str, str]],
    uncertain_keys: frozenset[tuple[str, str]],
) -> dict[str, int]:
    featured_keys = frozenset(confirmed_keys[: blueprint.metrics.interactions])
    plans_created = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO device_action_plans(
            round_id, identity_key, device_id, seed, requested_outcome,
            effective_outcome, quota_window_start_ms, quota_reason, video_key,
            state, created_at_ms, policy_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                blueprint.round_id,
                identity_key,
                device_id,
                f"{blueprint.namespace}:action:{sequence:05d}",
                requested_outcome,
                effective_outcome,
                quota_window_start_ms,
                quota_reason,
                video_key,
                (
                    "uncertain"
                    if (identity_key, device_id) in uncertain_keys
                    else "confirmed"
                ),
                plan_timestamp,
                "demo-portfolio-v1",
            )
            for sequence, (identity_key, device_id) in enumerate(confirmed_keys)
            for target_index in (_target_index(identity_key),)
            for eligible in (target_index <= blueprint.metrics.eligible,)
            for featured in ((identity_key, device_id) in featured_keys,)
            for requested_outcome in (
                _INTERACTION_OUTCOMES[sequence % len(_INTERACTION_OUTCOMES)]
                if featured
                else "trace",
            )
            for effective_outcome in (requested_outcome,)
            for plan_timestamp in (
                _assignment_base_timestamp(blueprint, identity_key) + 600,
            )
            for quota_window_start_ms in (
                (
                    plan_timestamp - plan_timestamp % 3_600_000
                    if effective_outcome != "trace"
                    else None
                ),
            )
            for quota_reason in (
                (
                    None
                    if effective_outcome != "trace"
                    else "pacing_not_due"
                    if eligible
                    else "profile_ineligible"
                ),
            )
            for video_key in (
                f"demo-video-action-{sequence + 1:05d}" if eligible else None,
            )
        ),
    )
    attempts_created = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO action_attempts(
            plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
        )
        SELECT plan.plan_id, 1, ?, ?, ?
        FROM device_action_plans AS plan
        WHERE plan.round_id = ? AND plan.identity_key = ? AND plan.device_id = ?
        """,
        (
            (
                (
                    "uncertain"
                    if (identity_key, device_id) in uncertain_keys
                    else "confirmed"
                ),
                json.dumps(
                    {"ui_summary": "DEMO synthetic visible-state evidence"},
                    separators=(",", ":"),
                ),
                plan_timestamp + 200,
                blueprint.round_id,
                identity_key,
                device_id,
            )
            for sequence, (identity_key, device_id) in enumerate(confirmed_keys)
            if sequence < blueprint.metrics.interactions
            for plan_timestamp in (
                _assignment_base_timestamp(blueprint, identity_key) + 600,
            )
        ),
    )
    video_history_created = _insert_video_confirmations(
        connection, blueprint, confirmed_keys
    )
    quota_windows_created = _insert_quota_windows(connection, blueprint)
    return {
        "action_plans": plans_created,
        "action_attempts": attempts_created,
        "video_confirmations": video_history_created,
        "quota_windows": quota_windows_created,
    }


def _insert_video_confirmations(
    connection: sqlite3.Connection,
    blueprint: DemoBlueprint,
    confirmed_keys: Sequence[tuple[str, str]],
) -> int:
    return _executemany_bounded(
        connection,
        """
        INSERT INTO assignment_phase_history(
            assignment_id, from_phase, to_phase, details_json, changed_at_ms
        )
        SELECT assignment.assignment_id, 'video_opening', 'video_confirmed', ?, ?
        FROM round_assignments AS assignment
        JOIN profile_snapshots AS snapshot
          ON snapshot.round_id = assignment.round_id
         AND snapshot.identity_key = assignment.identity_key
        WHERE assignment.round_id = ? AND assignment.identity_key = ?
          AND assignment.device_id = ? AND snapshot.eligible = 1
          AND NOT EXISTS (
              SELECT 1 FROM assignment_phase_history AS history
              WHERE history.assignment_id = assignment.assignment_id
                AND history.to_phase = 'video_confirmed'
          )
        """,
        (
            (
                json.dumps({"source": blueprint.namespace}, separators=(",", ":")),
                _assignment_base_timestamp(blueprint, identity_key) + 700,
                blueprint.round_id,
                identity_key,
                device_id,
            )
            for identity_key, device_id in confirmed_keys
        ),
    )


def _insert_quota_windows(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> int:
    return _execute_counted(
        connection,
        """
        INSERT OR IGNORE INTO acquisition_quota_windows(
            device_id, outcome, window_start_ms,
            reserved_count, confirmed_count, uncertain_count
        )
        SELECT device_id, effective_outcome, quota_window_start_ms,
               COUNT(*), SUM(state = 'confirmed'), SUM(state = 'uncertain')
        FROM device_action_plans
        WHERE round_id = ? AND effective_outcome <> 'trace'
        GROUP BY device_id, effective_outcome, quota_window_start_ms
        """,
        (blueprint.round_id,),
    )


def _seed_historical_evidence(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> dict[str, int]:
    history_rows = _demo_round_rows(blueprint)[1:]
    assignments_created = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO round_assignments(
            round_id, identity_key, device_id, order_key
        ) VALUES (?, ?, ?, ?)
        """,
        (
            (
                round_id,
                _history_identity(history_index),
                account.device_id,
                _order_key(
                    f"{blueprint.namespace}:{round_id}",
                    target_index=1,
                    device_id=account.device_id,
                ),
            )
            for history_index, (round_id, _state, _started_at_ms) in enumerate(
                history_rows, start=1
            )
            for account in blueprint.accounts
        ),
    )
    completed_created = _executemany_bounded(
        connection,
        """
        UPDATE round_assignments
        SET phase='completed', attempt_count=1,
            visit_confirmed_at_ms=?, completed_at_ms=?, last_error_code=NULL
        WHERE round_id=? AND identity_key=? AND device_id=?
          AND visit_confirmed_at_ms IS NULL
        """,
        (
            (
                started_at_ms + 400,
                started_at_ms + 5_000 + device_index * 100,
                round_id,
                _history_identity(history_index),
                account.device_id,
            )
            for history_index, (round_id, _state, round_started_at_ms) in enumerate(
                history_rows, start=1
            )
            for started_at_ms in (round_started_at_ms + _DAY_MS,)
            for device_index, account in enumerate(blueprint.accounts)
        ),
    )
    phase_history_created = _executemany_bounded(
        connection,
        """
        INSERT INTO assignment_phase_history(
            assignment_id, from_phase, to_phase, details_json, changed_at_ms
        )
        SELECT assignment.assignment_id, 'pending', 'profile_opening', ?, ?
        FROM round_assignments AS assignment
        WHERE assignment.round_id=? AND assignment.identity_key=?
          AND assignment.device_id=?
          AND NOT EXISTS (
              SELECT 1 FROM assignment_phase_history AS history
              WHERE history.assignment_id=assignment.assignment_id
                AND history.to_phase='profile_opening'
          )
        """,
        (
            (
                json.dumps({"source": blueprint.namespace}, separators=(",", ":")),
                round_started_at_ms + _DAY_MS,
                round_id,
                _history_identity(history_index),
                account.device_id,
            )
            for history_index, (round_id, _state, round_started_at_ms) in enumerate(
                history_rows, start=1
            )
            for account in blueprint.accounts
        ),
    )
    snapshots_created = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO profile_snapshots(
            round_id, identity_key, observed_by_device_id, observed_username,
            following_count, followers_count, post_count, private_account,
            access_state, eligible, reason, observed_at_ms
        ) VALUES (?, ?, ?, ?, 10, 20, 0, 0, 'public', 0,
                  'insufficient_posts', ?)
        """,
        (
            (
                round_id,
                _history_identity(history_index),
                blueprint.accounts[0].device_id,
                _history_username(history_index),
                round_started_at_ms + _DAY_MS + 500,
            )
            for history_index, (round_id, _state, round_started_at_ms) in enumerate(
                history_rows, start=1
            )
        ),
    )
    plans_created = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO device_action_plans(
            round_id, identity_key, device_id, seed, requested_outcome,
            effective_outcome, quota_window_start_ms, quota_reason, video_key,
            state, created_at_ms, policy_version
        ) VALUES (?, ?, ?, ?, 'trace', 'trace', NULL,
                  'profile_ineligible', NULL, 'confirmed', ?, 'demo-portfolio-v1')
        """,
        (
            (
                round_id,
                _history_identity(history_index),
                account.device_id,
                f"{blueprint.namespace}:history:{round_id}:{account.device_id}",
                round_started_at_ms + _DAY_MS + 600,
            )
            for history_index, (round_id, _state, round_started_at_ms) in enumerate(
                history_rows, start=1
            )
            for account in blueprint.accounts
        ),
    )
    return {
        "historical_assignments": assignments_created,
        "historical_confirmed_visits": completed_created,
        "historical_phase_history": phase_history_created,
        "historical_snapshots": snapshots_created,
        "historical_action_plans": plans_created,
    }


def _insert_device_health(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> int:
    degraded_index = len(blueprint.accounts) - 1
    return _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO fleet_device_health(
            device_id, account_id, state, owner_id, fence_token,
            process_id, error_code, updated_at_ms
        ) VALUES (?, ?, ?, NULL, 0, NULL, ?, ?)
        """,
        (
            (
                account.device_id,
                account.account_id,
                "unhealthy" if index == degraded_index else "healthy",
                "demo_capacity_gap" if index == degraded_index else None,
                blueprint.now_ms - (index + 1) * 1_000,
            )
            for index, account in enumerate(blueprint.accounts)
        ),
    )


def _insert_operator_controls(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> int:
    return _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO operator_control_states(
            scope, scope_id, state, updated_at_ms, command_id
        ) VALUES ('device', ?, 'stopped', ?, ?)
        """,
        (
            (
                account.device_id,
                blueprint.now_ms,
                f"{blueprint.namespace}:stop:{account.device_id}",
            )
            for account in blueprint.accounts
        ),
    )


def _confirmed_assignment_keys(
    blueprint: DemoBlueprint,
) -> tuple[tuple[str, str], ...]:
    required = blueprint.metrics.confirmed_visits
    keys: list[tuple[str, str]] = []
    for target_index in range(1, blueprint.metrics.fully_covered + 1):
        identity_key = _target_identity(target_index)
        keys.extend((identity_key, account.device_id) for account in blueprint.accounts)
    remainder = required - len(keys)
    if remainder < 0:
        raise ValueError("confirmed visits are below fully covered projection")
    uncovered_target_indexes = range(
        blueprint.metrics.fully_covered + 1, len(blueprint.targets) + 1
    )
    uncovered = (
        (_target_identity(target_index), account.device_id)
        for account in blueprint.accounts
        for target_index in uncovered_target_indexes
    )
    keys.extend(_take(uncovered, remainder))
    if len(keys) != required:
        raise ValueError("confirmed visits exceed assignment projection")
    return tuple(keys)


def _uncertain_assignment_keys(
    blueprint: DemoBlueprint,
    confirmed_keys: Sequence[tuple[str, str]],
) -> frozenset[tuple[str, str]]:
    selected: list[tuple[str, str]] = []
    for key in confirmed_keys[: blueprint.metrics.interactions]:
        selected.append(key)
        if len(selected) == blueprint.metrics.ai_uncertain:
            break
    if len(selected) != blueprint.metrics.ai_uncertain:
        raise ValueError("uncertain plans exceed non-trace interaction projection")
    return frozenset(selected)


def _take(rows: Iterable[tuple[str, str]], count: int) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for row in rows:
        if len(result) == count:
            break
        result.append(row)
    return result


def _demo_started_at_ms(blueprint: DemoBlueprint) -> int:
    return max(1, blueprint.now_ms - 13 * _DAY_MS)


def _demo_round_rows(
    blueprint: DemoBlueprint,
) -> tuple[tuple[str, str, int], ...]:
    main_started_at_ms = _demo_started_at_ms(blueprint)
    return (
        (blueprint.round_id, "stopped", main_started_at_ms),
        (
            DEMO_HISTORY_ROUND_IDS[0],
            "completed",
            max(1, main_started_at_ms - 14 * _DAY_MS),
        ),
        (
            DEMO_HISTORY_ROUND_IDS[1],
            "completed",
            max(1, main_started_at_ms - 28 * _DAY_MS),
        ),
    )


def _assignment_base_timestamp(blueprint: DemoBlueprint, identity_key: str) -> int:
    started_at_ms = _demo_started_at_ms(blueprint)
    usable_span_ms = max(0, blueprint.now_ms - started_at_ms - 7_000)
    target_index = _target_index(identity_key)
    return started_at_ms + (target_index - 1) * usable_span_ms // max(
        1, len(blueprint.targets)
    )


def _assignment_completed_timestamp(
    blueprint: DemoBlueprint, identity_key: str, sequence: int
) -> int:
    return _assignment_base_timestamp(
        blueprint, identity_key
    ) + _assignment_duration_ms(sequence)


def _assignment_duration_ms(sequence: int) -> int:
    return 4_100 + sequence % 2_200


def _target_identity(index: int) -> str:
    return f"{DEMO_POOL_IDENTITY_PREFIX}{index:05d}"


def _history_identity(index: int) -> str:
    return f"demo:history:{index:02d}:target:00001"


def _history_username(index: int) -> str:
    return f"demo_history_{index:02d}_target"


def _round_pool_id(blueprint: DemoBlueprint, round_id: str) -> str:
    if round_id == blueprint.round_id:
        return blueprint.pool_id
    return DEMO_HISTORY_POOL_IDS[DEMO_HISTORY_ROUND_IDS.index(round_id)]


def _target_index(identity_key: str) -> int:
    return int(identity_key.removeprefix(DEMO_POOL_IDENTITY_PREFIX))


def _order_key(namespace: str, *, target_index: int, device_id: str) -> str:
    return hashlib.sha256(
        f"{namespace}\0{device_id}\0{target_index}".encode()
    ).hexdigest()


def _execute_counted(
    connection: sqlite3.Connection, sql: str, parameters: Sequence[object]
) -> int:
    before = connection.total_changes
    connection.execute(sql, parameters)
    return connection.total_changes - before


def _executemany_bounded(
    connection: sqlite3.Connection,
    sql: str,
    rows: Iterable[Sequence[object]],
    *,
    batch_size: int = _SQL_BATCH_SIZE,
) -> int:
    created = 0
    batch: list[Sequence[object]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == batch_size:
            before = connection.total_changes
            connection.executemany(sql, batch)
            created += connection.total_changes - before
            batch.clear()
    if batch:
        before = connection.total_changes
        connection.executemany(sql, batch)
        created += connection.total_changes - before
    return created


def _build_blueprint(scale: DemoScale, *, now_ms: int, seed: int) -> DemoBlueprint:
    rng = random.Random(seed)
    accounts = tuple(_build_account(index) for index in range(1, scale.devices + 1))
    metrics = DemoMetrics(
        assignments=scale.assignments,
        confirmed_visits=scale.confirmed_visits,
        fully_covered=scale.fully_covered,
        eligible=scale.eligible,
        interactions=scale.interactions,
        followers=scale.followers,
        inbound=scale.inbound,
        engaged=scale.engaged,
        qualified=scale.qualified,
        invited=scale.invited,
        contact_captured=scale.contact_captured,
        human_required=scale.human_required,
        sales=scale.sales,
        ai_plans=scale.ai_plans,
        ai_sent=scale.ai_sent,
        ai_uncertain=scale.ai_uncertain,
        ai_superseded=scale.ai_superseded,
    )
    timeline = _build_timeline(metrics, now_ms=now_ms, rng=rng)
    conversations = _build_conversations(
        scale.conversations,
        accounts=accounts,
        now_ms=now_ms,
        rng=rng,
        human_required=scale.human_required,
    )
    return DemoBlueprint(
        namespace=DEMO_NAMESPACE,
        pool_id=DEMO_POOL_ID,
        round_id=DEMO_ROUND_ID,
        label=DEMO_ROUND_LABEL,
        now_ms=now_ms,
        scale=scale,
        targets=tuple(
            f"demo_target_{index:05d}" for index in range(1, scale.targets + 1)
        ),
        accounts=accounts,
        conversations=conversations,
        timeline=timeline,
        metrics=metrics,
    )


def _build_account(index: int) -> DemoAccount:
    return DemoAccount(
        account_id=f"demo-account-{index:02d}",
        device_id=f"demo-device-{index:02d}",
        profile_label=f"DEMO Profile {index:02d}",
        username=f"demo_shop_{index:02d}",
        private_channel_hint=f"https://example.invalid/demo-channel/{index:02d}",
    )


def _conversion_account(blueprint: DemoBlueprint, index: int) -> DemoAccount:
    if index <= len(blueprint.conversations):
        account_id = blueprint.conversations[index - 1].account_id
        return next(
            account
            for account in blueprint.accounts
            if account.account_id == account_id
        )
    return blueprint.accounts[(index - 1) % len(blueprint.accounts)]


def _conversation_key(blueprint: DemoBlueprint, index: int) -> str:
    if index <= len(blueprint.conversations):
        return blueprint.conversations[index - 1].conversation_key
    return f"demo:conversation:{index:04d}"


def _lead_id(blueprint: DemoBlueprint, index: int) -> str:
    if index <= len(blueprint.conversations):
        return blueprint.conversations[index - 1].lead_id
    return f"demo_lead_{index:04d}"


def _inbound_fingerprint(index: int) -> str:
    return f"demo:inbound:{index:04d}"


def _conversion_timestamp(
    blueprint: DemoBlueprint,
    index: int,
    *,
    total: int | None = None,
) -> int:
    selected_total = blueprint.metrics.inbound if total is None else int(total)
    if selected_total < 1 or index < 1 or index > selected_total:
        raise ValueError("demo conversion position is outside its timeline")
    end_day_ms = blueprint.now_ms // _DAY_MS * _DAY_MS
    start_ms = max(1, end_day_ms - 13 * _DAY_MS)
    window_ms = blueprint.now_ms - start_ms + 1
    return start_ms + ((index - 1) * window_ms) // selected_total


def _aggregate_inbound_text(index: int) -> str:
    if index % 2:
        return "请介绍演示商品的规格和交付方式。"
    return "Could you share the DEMO product options and delivery details?"


def _aggregate_outbound_text(index: int) -> str:
    if index % 2:
        return "可以，这是合成演示数据；您更关心哪个规格？"
    return "Sure—this is synthetic DEMO data. Which option matters most?"


def _conversation_stage(
    blueprint: DemoBlueprint,
    index: int,
    detailed: Mapping[int, DemoConversation],
    human_indexes: frozenset[int],
) -> str:
    if index in human_indexes:
        return "human_required"
    if index in detailed:
        return detailed[index].stage
    if index <= blueprint.metrics.sales:
        return "closed"
    if index <= blueprint.metrics.contact_captured:
        return "contact_captured"
    if index <= blueprint.metrics.invited:
        return "invited"
    if index <= blueprint.metrics.qualified:
        return "qualified"
    if index <= blueprint.metrics.engaged:
        return "engaged"
    return "new"


def _conversion_decisions(
    blueprint: DemoBlueprint,
    detailed: Mapping[int, DemoConversation],
) -> tuple[dict[int, str], tuple[int, ...], frozenset[int]]:
    inbound_indexes = tuple(range(1, blueprint.metrics.inbound + 1))
    human_candidates = [
        index for index, item in detailed.items() if item.stage == "human_required"
    ]
    human_candidates.extend(
        index for index in reversed(inbound_indexes) if index not in human_candidates
    )
    human_indexes = frozenset(human_candidates[: blueprint.metrics.human_required])
    available = [
        index
        for index in inbound_indexes
        if index not in human_indexes
        and not (index in detailed and detailed[index].stage == "new")
    ]
    preferred_sent = [index for index in (1, 4) if index in available]
    reserved_detailed = {2, 3, *preferred_sent}
    uncertain = ([2] if blueprint.metrics.ai_uncertain and 2 in available else []) + [
        index for index in available if index not in reserved_detailed
    ][: max(0, blueprint.metrics.ai_uncertain - 1)]
    preferred_superseded = bool(
        blueprint.metrics.ai_superseded and 3 in available and 3 not in uncertain
    )
    superseded = ([3] if preferred_superseded else []) + [
        index
        for index in available
        if index not in uncertain and index not in reserved_detailed
    ][: max(0, blueprint.metrics.ai_superseded - int(preferred_superseded))]
    remaining = [
        index
        for index in available
        if index not in uncertain
        and index not in superseded
        and index not in preferred_sent
    ]
    sent = (preferred_sent + remaining)[: blueprint.metrics.ai_sent]
    ai_states = {
        **dict.fromkeys(sent, "sent"),
        **dict.fromkeys(uncertain, "uncertain"),
        **dict.fromkeys(superseded, "superseded"),
    }
    manual_indexes = tuple(index for index in available if index not in ai_states)[
        : blueprint.scale.manual_handled
    ]
    return ai_states, manual_indexes, human_indexes


def _build_timeline(
    metrics: DemoMetrics,
    *,
    now_ms: int,
    rng: random.Random,
) -> tuple[DemoTimelineDay, ...]:
    weights = [2, 3, 4, 5, 7, 8, 10, 11, 12, 12, 10, 8, 5, 3]
    inbound = _distribute(metrics.inbound, weights, rng=rng)
    engaged = _distribute(metrics.engaged, weights, rng=rng)
    qualified = _distribute(metrics.qualified, weights, rng=rng)
    invited = _distribute(metrics.invited, weights, rng=rng)
    captured = _distribute(metrics.contact_captured, weights, rng=rng)
    human = _distribute(metrics.human_required, weights, rng=rng)
    sales = _distribute(metrics.sales, weights, rng=rng)
    end_day_ms = (now_ms // _DAY_MS) * _DAY_MS
    result: list[DemoTimelineDay] = []
    for index in range(14):
        started_at_ms = max(1, end_day_ms - (13 - index) * _DAY_MS)
        date = datetime.fromtimestamp(started_at_ms / 1000, tz=UTC).date().isoformat()
        result.append(
            DemoTimelineDay(
                date=date,
                started_at_ms=started_at_ms,
                dm_inbound=inbound[index],
                engaged=engaged[index],
                qualified=qualified[index],
                invited=invited[index],
                contact_captured=captured[index],
                human_required=human[index],
                sales=sales[index],
            )
        )
    return tuple(result)


def _distribute(total: int, weights: list[int], *, rng: random.Random) -> list[int]:
    weight_total = sum(weights)
    values = [total * weight // weight_total for weight in weights]
    remaining = total - sum(values)
    candidates = list(range(len(weights)))
    rng.shuffle(candidates)
    candidates.sort(
        key=lambda index: total * weights[index] % weight_total, reverse=True
    )
    for index in candidates[:remaining]:
        values[index] += 1
    return values


def _build_conversations(
    count: int,
    *,
    accounts: tuple[DemoAccount, ...],
    now_ms: int,
    rng: random.Random,
    human_required: int,
) -> tuple[DemoConversation, ...]:
    templates = (
        ("zh", "qualified", "请问这个演示商品还有吗？", "有的，您更关心哪个规格？"),
        (
            "en",
            "engaged",
            "Can you share the demo product details?",
            "Sure. Which feature matters most to you?",
        ),
        ("zh", "invited", "我想进一步了解。", "可以通过演示私域渠道继续沟通。"),
        (
            "en",
            "contact_captured",
            "My demo contact is demo@example.invalid",
            "Thanks, your demo contact is recorded.",
        ),
        ("zh", "human_required", "请安排人工客服。", "已标记为演示人工接管。"),
        (
            "en",
            "human_required",
            "I need help with a demo refund.",
            "A demo specialist will review this conversation.",
        ),
        ("zh", "closed", "已确认演示订单。", "感谢您，演示成交已记录。"),
        (
            "zh",
            "human_required",
            "我想取消演示订单，请安排人工处理。",
            "已标记为演示人工接管。",
        ),
        (
            "en",
            "human_required",
            "I have a complaint about the demo order.",
            "A demo specialist will review this conversation.",
        ),
        (
            "en",
            "new",
            "Hello, I just found this demo catalog.",
            "Thanks for reaching out to the DEMO catalog.",
        ),
    )
    conversations: list[DemoConversation] = []
    human_count = 0
    for index in range(1, count + 1):
        language, stage, inbound_text, outbound_text = templates[
            (index - 1) % len(templates)
        ]
        if stage == "human_required":
            human_count += 1
            if human_count > human_required:
                stage = "engaged"
                inbound_text = (
                    "请介绍一下演示商品。"
                    if language == "zh"
                    else "Could you tell me more about the demo product?"
                )
                outbound_text = (
                    "可以，您最关注哪个特点？"
                    if language == "zh"
                    else "Sure. Which feature matters most?"
                )
        account = accounts[rng.randrange(len(accounts))]
        offset_ms = rng.randrange(0, 14 * _DAY_MS)
        conversations.append(
            DemoConversation(
                lead_id=f"demo_lead_{index:03d}",
                conversation_key=f"demo:conversation:{index:03d}",
                account_id=account.account_id,
                language=language,
                stage=stage,
                inbound_text=inbound_text,
                outbound_text=outbound_text,
                occurred_at_ms=max(1, now_ms - offset_ms),
            )
        )
    return tuple(conversations)
