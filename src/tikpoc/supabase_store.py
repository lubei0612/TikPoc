import json
import stat
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .importer import Target, target_identity_key


class SupabaseBusinessStore:
    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        opener: Callable[..., object] = urlopen,
        batch_size: int = 500,
        timeout: float = 30.0,
    ) -> None:
        self.url = str(url).strip().rstrip("/")
        self._service_role_key = str(service_role_key).strip()
        if not self.url.startswith("https://") or not self._service_role_key:
            raise ValueError("Supabase URL and service role key are required")
        self.opener = opener
        self.batch_size = max(1, min(int(batch_size), 1_000))
        self.timeout = max(1.0, float(timeout))

    def __repr__(self) -> str:
        return f"SupabaseBusinessStore(url={self.url!r}, batch_size={self.batch_size})"

    @classmethod
    def from_env_file(
        cls,
        path: Path,
        **kwargs: object,
    ) -> "SupabaseBusinessStore":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ValueError("Supabase environment file must be owner-only")
        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
        return cls(
            values.get("SUPABASE_URL", ""),
            values.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            **kwargs,
        )

    def import_pool(
        self,
        *,
        pool_id: str,
        source_name: str,
        source_checksum: str,
        source_rows: int,
        targets: tuple[Target, ...],
    ) -> None:
        if not targets:
            raise ValueError("target pool is empty")
        importing_pool = {
            "pool_id": pool_id,
            "source_name": source_name,
            "source_checksum": source_checksum,
            "source_rows": 0,
            "unique_targets": 0,
            "import_state": "importing",
        }
        self._upsert(
            "tikpoc_target_pools",
            [importing_pool],
            on_conflict="pool_id",
        )
        self._delete_pool_targets(pool_id)
        rows = (self._target_row(pool_id, target) for target in targets)
        for batch in _batches(rows, self.batch_size):
            self._upsert(
                "tikpoc_targets",
                batch,
                on_conflict="pool_id,identity_key",
            )
        self._upsert(
            "tikpoc_target_pools",
            [
                {
                    **importing_pool,
                    "source_rows": int(source_rows),
                    "unique_targets": len(targets),
                    "import_state": "complete",
                }
            ],
            on_conflict="pool_id",
        )

    def upsert_accounts(
        self,
        accounts: Iterable[dict[str, object]],
        *,
        observed_at: str,
    ) -> None:
        rows = [{**account, "updated_at": observed_at} for account in accounts]
        if rows:
            self._upsert("tikpoc_accounts", rows, on_conflict="account_id")

    def upsert_device_health(
        self,
        devices: Iterable[dict[str, object]],
        *,
        observed_at: str,
    ) -> None:
        rows = [{**device, "observed_at": observed_at} for device in devices]
        if rows:
            self._upsert("tikpoc_device_health", rows, on_conflict="device_id")

    def _target_row(self, pool_id: str, target: Target) -> dict[str, object]:
        metrics = target.profile_metrics
        identity_key = target.identity_key or target_identity_key(
            sec_uid=target.sec_uid,
            target_id=target.target_id,
            username=target.username,
        )
        return {
            "pool_id": pool_id,
            "identity_key": identity_key,
            "target_id": target.target_id,
            "username": target.username,
            "profile_url": target.profile_url,
            "sec_uid": target.sec_uid,
            "source_video_id": target.source_video_id,
            "follower_count": None if metrics is None else metrics.followers,
            "following_count": None if metrics is None else metrics.following,
            "video_count": None if metrics is None else metrics.posts,
            "private_account": target.private_account,
            "source_line_numbers": list(target.source_line_numbers),
        }

    def _upsert(
        self,
        table: str,
        rows: list[dict[str, object]],
        *,
        on_conflict: str,
    ) -> None:
        query = urlencode({"on_conflict": on_conflict}, safe=",")
        request = Request(
            f"{self.url}/rest/v1/{table}?{query}",
            data=json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode(),
            headers={
                "apikey": self._service_role_key,
                "Authorization": f"Bearer {self._service_role_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST",
        )
        self._send(request, table)

    def _delete_pool_targets(self, pool_id: str) -> None:
        query = urlencode({"pool_id": f"eq.{pool_id}"})
        request = Request(
            f"{self.url}/rest/v1/tikpoc_targets?{query}",
            headers={
                "apikey": self._service_role_key,
                "Authorization": f"Bearer {self._service_role_key}",
                "Prefer": "return=minimal",
            },
            method="DELETE",
        )
        self._send(request, "tikpoc_targets")

    def _send(self, request: Request, table: str) -> None:
        try:
            response = self.opener(request, timeout=self.timeout)
            status_code = int(getattr(response, "status", 200))
            close = getattr(response, "close", None)
            if callable(close):
                close()
        except HTTPError as error:
            raise RuntimeError(
                f"Supabase {table} request failed with HTTP {error.code}"
            ) from None
        except URLError:
            raise RuntimeError(f"Supabase {table} request failed") from None
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(
                f"Supabase {table} request failed with HTTP {status_code}"
            )


def _batches(
    rows: Iterable[dict[str, object]], batch_size: int
) -> Iterable[list[dict[str, object]]]:
    batch: list[dict[str, object]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
