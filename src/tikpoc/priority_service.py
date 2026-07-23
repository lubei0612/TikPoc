import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from .acquisition_db import AcquisitionRepository
from .acquisition_models import PriorityBatchClass
from .fleet import FleetConfig
from .priority_importer import read_priority_targets


@dataclass(frozen=True)
class PriorityImportSummary:
    batch_id: str
    parent_round_id: str
    unique_targets: int
    skipped_duplicates: int
    skipped_invalid: int
    device_count: int


class PriorityBatchService:
    def __init__(self, repository: AcquisitionRepository) -> None:
        self.repository = repository

    def import_batch(
        self,
        source_path: Path,
        *,
        source_live_id: str,
        fleet_config: FleetConfig,
    ) -> PriorityImportSummary:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"priority input does not exist: {source}")
        live_id = str(source_live_id).strip()
        if not live_id:
            raise ValueError("source live id is required")
        before = source.stat()
        configured_device_ids = tuple(
            sorted(device.device_id for device in fleet_config.devices)
        )
        try:
            parsed = read_priority_targets(source, source_live_id=live_id)
        except (BadZipFile, InvalidFileException, ParseError) as error:
            raise ValueError("priority workbook is invalid") from error
        source_bytes = source.read_bytes()
        after = source.stat()
        before_signature = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_signature = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_signature != after_signature or len(source_bytes) != after.st_size:
            raise ValueError("priority input changed while reading")
        checksum = hashlib.sha256(source_bytes).hexdigest()
        parent_rounds = self._active_ordinary_round_ids()
        if not parent_rounds:
            return self._replay_completed_batch(
                checksum=checksum,
                source_live_id=live_id,
                configured_device_ids=configured_device_ids,
                unique_targets=len(parsed.targets),
                skipped_duplicates=parsed.skipped_duplicates,
                skipped_invalid=parsed.skipped_invalid,
            )
        if len(parent_rounds) != 1:
            raise ValueError(
                "priority import requires exactly one active ordinary round"
            )
        parent_round_id = parent_rounds[0]
        if configured_device_ids != self.repository.round_device_ids(parent_round_id):
            raise ValueError("device ids do not match active ordinary round")

        existing = self.repository.priority_batch_for_source(
            parent_round_id, live_id, checksum
        )
        if existing is not None:
            return PriorityImportSummary(
                batch_id=existing.batch_id,
                parent_round_id=existing.parent_round_id,
                unique_targets=len(parsed.targets),
                skipped_duplicates=parsed.skipped_duplicates,
                skipped_invalid=parsed.skipped_invalid,
                device_count=len(
                    self.repository.round_device_ids(existing.priority_round_id)
                ),
            )

        participant_device_ids = self.repository.running_round_device_ids(
            parent_round_id
        )
        if not participant_device_ids:
            raise ValueError("priority import requires at least one running device")

        imported = self.repository.import_pool(source.name, checksum, parsed.targets)
        batch_digest = hashlib.sha256(
            "\0".join((parent_round_id, live_id, checksum)).encode()
        ).hexdigest()
        batch_id = f"priority-{batch_digest[:16]}"
        device_seeds = {
            device_id: hashlib.sha256(
                "\0".join((batch_id, device_id)).encode()
            ).hexdigest()
            for device_id in participant_device_ids
        }
        self.repository.create_priority_batch(
            batch_id=batch_id,
            parent_round_id=parent_round_id,
            pool_id=imported.pool_id,
            source_live_id=live_id,
            source_checksum=checksum,
            device_seeds=device_seeds,
            batch_class=PriorityBatchClass.LIVE_INTERRUPT,
            require_unique_active_parent=True,
        )
        return PriorityImportSummary(
            batch_id=batch_id,
            parent_round_id=parent_round_id,
            unique_targets=imported.unique_targets,
            skipped_duplicates=parsed.skipped_duplicates,
            skipped_invalid=parsed.skipped_invalid,
            device_count=len(participant_device_ids),
        )

    def _replay_completed_batch(
        self,
        *,
        checksum: str,
        source_live_id: str,
        configured_device_ids: tuple[str, ...],
        unique_targets: int,
        skipped_duplicates: int,
        skipped_invalid: int,
    ) -> PriorityImportSummary:
        with self.repository._connect_read_only() as connection:
            rows = connection.execute(
                """
                SELECT batch_id, parent_round_id, priority_round_id
                FROM priority_batches
                WHERE source_checksum = ? AND source_live_id = ?
                ORDER BY queue_sequence
                """,
                (checksum, source_live_id),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                "priority import requires exactly one active ordinary round"
            )
        row = rows[0]
        stored_device_ids = self.repository.round_device_ids(
            str(row["priority_round_id"])
        )
        if not set(stored_device_ids).issubset(configured_device_ids):
            raise ValueError("device ids do not match replayed priority batch")
        return PriorityImportSummary(
            batch_id=str(row["batch_id"]),
            parent_round_id=str(row["parent_round_id"]),
            unique_targets=unique_targets,
            skipped_duplicates=skipped_duplicates,
            skipped_invalid=skipped_invalid,
            device_count=len(stored_device_ids),
        )

    def status(self) -> dict[str, object]:
        with self.repository._connect_read_only() as connection:
            connection.execute("BEGIN")
            batches = connection.execute(
                "SELECT * FROM priority_batches ORDER BY queue_sequence"
            ).fetchall()
            active_rounds = self._active_ordinary_round_ids(connection=connection)
            if len(active_rounds) > 1:
                raise ValueError(
                    "priority status found multiple active ordinary rounds"
                )
            parent_round_id = active_rounds[0] if len(active_rounds) == 1 else None
            if parent_round_id is None and batches:
                parent_round_id = str(batches[-1]["parent_round_id"])
            batch_rows = []
            for batch in batches:
                device_rows = connection.execute(
                    """
                    SELECT device_id, COUNT(*) AS total,
                           SUM(phase = 'completed') AS completed,
                           SUM(phase = 'skipped') AS skipped,
                           SUM(phase = 'deferred') AS deferred,
                           SUM(phase NOT IN ('completed','skipped','deferred')) AS pending
                    FROM round_assignments
                    WHERE round_id = ?
                    GROUP BY device_id ORDER BY device_id
                    """,
                    (str(batch["priority_round_id"]),),
                ).fetchall()
                batch_rows.append(
                    {
                        "batch_id": str(batch["batch_id"]),
                        "parent_round_id": str(batch["parent_round_id"]),
                        "queue_sequence": int(batch["queue_sequence"]),
                        "source_live_id": str(batch["source_live_id"]),
                        "state": str(batch["state"]),
                        "devices": [
                            {
                                "device_id": str(row["device_id"]),
                                "total": int(row["total"]),
                                "pending": int(row["pending"] or 0),
                                "completed": int(row["completed"] or 0),
                                "skipped": int(row["skipped"] or 0),
                                "deferred": int(row["deferred"] or 0),
                            }
                            for row in device_rows
                        ],
                    }
                )
            checkpoint = None
            if parent_round_id is not None:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(visit_confirmed_at_ms IS NOT NULL) AS visits_confirmed,
                           SUM(phase = 'completed') AS completed,
                           SUM(phase = 'skipped') AS skipped,
                           SUM(phase = 'deferred') AS deferred
                    FROM round_assignments WHERE round_id = ?
                    """,
                    (parent_round_id,),
                ).fetchone()
                total = int(row["total"] or 0)
                completed = int(row["completed"] or 0)
                skipped = int(row["skipped"] or 0)
                deferred = int(row["deferred"] or 0)
                checkpoint = {
                    "parent_round_id": parent_round_id,
                    "total": total,
                    "pending": max(0, total - completed - skipped - deferred),
                    "completed": completed,
                    "skipped": skipped,
                    "deferred": deferred,
                    "visits_confirmed": int(row["visits_confirmed"] or 0),
                }
        return {"batches": batch_rows, "ordinary_checkpoint": checkpoint}

    def _active_ordinary_round_ids(self, *, connection=None) -> tuple[str, ...]:
        if connection is None:
            with self.repository._connect_read_only() as active_connection:
                return self._active_ordinary_round_ids(connection=active_connection)
        rows = connection.execute(
            """
            SELECT round.round_id
            FROM exposure_rounds AS round
            WHERE round.state IN ('pending','running')
              AND NOT EXISTS (
                  SELECT 1 FROM priority_batches AS batch
                  WHERE batch.priority_round_id = round.round_id
              )
            ORDER BY round.created_at_ms, round.round_id
            """
        ).fetchall()
        return tuple(str(row["round_id"]) for row in rows)


def summary_json(summary: PriorityImportSummary) -> dict[str, object]:
    return asdict(summary)
