import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .acquisition_db import AcquisitionRepository
from .acquisition_models import PriorityBatchClass
from .importer import Target, target_identity_key
from .navigation import NavigationMode

_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._]{1,24}")


@dataclass(frozen=True)
class LiveTargetInput:
    username: str
    sec_uid: str = ""
    uid: str = ""
    source_video_id: str = ""
    collected_at_ms: int = 0


@dataclass(frozen=True)
class LiveBatchSummary:
    batch_id: str
    source_live_id: str
    unique_targets: int
    skipped_duplicates: int
    skipped_invalid: int
    device_count: int
    navigation_mode: str


class LiveBatchService:
    def __init__(self, repository: AcquisitionRepository) -> None:
        self.repository = repository

    def submit(
        self,
        *,
        host_round_id: str,
        source_live_id: str,
        targets: Iterable[LiveTargetInput],
        navigation_mode: NavigationMode | str = NavigationMode.DEEPLINK,
    ) -> LiveBatchSummary:
        host = str(host_round_id).strip()
        live_id = str(source_live_id).strip()
        navigation = NavigationMode.parse(str(navigation_mode))
        if not host or not live_id:
            raise ValueError("live batch identifiers are required")
        normalized, duplicates, invalid = self._normalize(targets)
        if not normalized:
            raise ValueError("live batch has no valid target")
        canonical = "".join(
            json.dumps(
                {
                    "identity_key": target.identity_key,
                    "profile_url": target.profile_url,
                    "sec_uid": target.sec_uid,
                    "source_video_id": target.source_video_id,
                    "target_id": target.target_id,
                    "username": target.username,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for target in normalized
        ).encode()
        checksum = hashlib.sha256(canonical).hexdigest()
        existing = self.repository.priority_batch_for_source(host, live_id, checksum)
        if existing is not None:
            return LiveBatchSummary(
                batch_id=existing.batch_id,
                source_live_id=live_id,
                unique_targets=len(normalized),
                skipped_duplicates=duplicates,
                skipped_invalid=invalid,
                device_count=len(
                    self.repository.priority_batch_device_ids(existing.batch_id)
                ),
                navigation_mode=existing.navigation_mode,
            )
        devices = self.repository.running_round_device_ids(host)
        if not devices:
            raise ValueError("live batch requires at least one running device")
        imported = self.repository.import_pool(
            f"live:{live_id}.jsonl", checksum, normalized
        )
        batch_digest = hashlib.sha256(
            f"{host}\0{live_id}\0{checksum}".encode()
        ).hexdigest()
        batch_id = f"priority-{batch_digest[:16]}"
        seeds = {
            device_id: hashlib.sha256(f"{batch_id}\0{device_id}".encode()).hexdigest()
            for device_id in devices
        }
        batch = self.repository.create_priority_batch(
            batch_id=batch_id,
            parent_round_id=host,
            pool_id=imported.pool_id,
            source_live_id=live_id,
            source_checksum=checksum,
            device_seeds=seeds,
            batch_class=PriorityBatchClass.LIVE_INTERRUPT,
            navigation_mode=navigation,
        )
        return LiveBatchSummary(
            batch_id=batch.batch_id,
            source_live_id=live_id,
            unique_targets=imported.unique_targets,
            skipped_duplicates=duplicates,
            skipped_invalid=invalid,
            device_count=len(devices),
            navigation_mode=navigation.value,
        )

    @staticmethod
    def _normalize(
        inputs: Iterable[LiveTargetInput],
    ) -> tuple[tuple[Target, ...], int, int]:
        targets: list[Target] = []
        aliases: dict[str, int] = {}
        duplicates = 0
        invalid = 0
        for line_number, value in enumerate(inputs, start=1):
            username = str(value.username or "").strip().removeprefix("@").lower()
            if _USERNAME_PATTERN.fullmatch(username) is None:
                invalid += 1
                continue
            sec_uid = str(value.sec_uid or "").strip()
            uid = str(value.uid or "").strip()
            target = Target(
                target_id=uid,
                username=username,
                profile_url=f"https://www.tiktok.com/@{username}",
                source_video_id=str(value.source_video_id or "").strip(),
                sec_uid=sec_uid,
                identity_key=target_identity_key(
                    sec_uid=sec_uid, target_id=uid, username=username
                ),
                source_line_numbers=(line_number,),
            )
            target_aliases = LiveBatchService._aliases(target)
            matches = {aliases[alias] for alias in target_aliases if alias in aliases}
            if not matches:
                index = len(targets)
                targets.append(target)
                for alias in target_aliases:
                    aliases[alias] = index
                continue
            index = min(matches)
            existing = targets[index]
            if (
                existing.sec_uid
                and target.sec_uid
                and existing.sec_uid != target.sec_uid
            ) or (
                existing.target_id
                and target.target_id
                and existing.target_id != target.target_id
            ):
                raise ValueError("live target has conflicting identity")
            merged = replace(
                existing,
                target_id=target.target_id or existing.target_id,
                sec_uid=target.sec_uid or existing.sec_uid,
                source_video_id=target.source_video_id or existing.source_video_id,
                identity_key=target_identity_key(
                    sec_uid=target.sec_uid or existing.sec_uid,
                    target_id=target.target_id or existing.target_id,
                    username=existing.username,
                ),
                source_line_numbers=(
                    existing.source_line_numbers + target.source_line_numbers
                ),
            )
            targets[index] = merged
            for alias in LiveBatchService._aliases(merged):
                aliases[alias] = index
            duplicates += 1
        return (
            tuple(sorted(targets, key=lambda item: item.identity_key)),
            duplicates,
            invalid,
        )

    @staticmethod
    def _aliases(target: Target) -> tuple[str, ...]:
        values = [f"handle:{target.username}"]
        if target.sec_uid:
            values.append(f"sec:{target.sec_uid}")
        if target.target_id:
            values.append(f"uid:{target.target_id}")
        return tuple(values)
