import hashlib
import json
import random
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .acquisition_db import AcquisitionRepository
from .db import Database

DEMO_NAMESPACE = "demo-ai-growth-v1"
DEMO_POOL_ID = "demo-pool-ai-growth-v1"
DEMO_ROUND_ID = "demo-round-ai-growth-v1"
DEMO_POOL_IDENTITY_PREFIX = "demo:target:"
DEMO_ROUND_LABEL = "DEMO · AI 多账号获客转化试点"
DEMO_SEED = 20260904
_DAY_MS = 86_400_000
_SQL_BATCH_SIZE = 1_000
_ACTION_OUTCOMES = ("like", "favorite", "repost", "trace")


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


def seed_demo_database(path: Path, blueprint: DemoBlueprint) -> DemoSeedResult:
    """Persist one deterministic demo dataset without activating any worker."""
    database_path = Path(path)
    AcquisitionRepository(database_path).migrate()
    Database(database_path).migrate()

    connection = sqlite3.connect(database_path, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        created = _seed_acquisition(connection, blueprint)
        conversion_created = _seed_conversion(connection, blueprint)
        for name, count in conversion_created.items():
            created[name] = created.get(name, 0) + count
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return DemoSeedResult(created=created)


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
    created["rounds"] = _execute_counted(
        connection,
        """
        INSERT OR IGNORE INTO exposure_rounds(
            round_id, pool_id, state, starts_at_ms,
            min_inter_device_gap_ms, min_repeat_gap_ms, created_at_ms,
            navigation_mode
        ) VALUES (?, ?, 'stopped', ?, 0, 0, ?, 'deeplink')
        """,
        (
            blueprint.round_id,
            blueprint.pool_id,
            _demo_started_at_ms(blueprint),
            _demo_started_at_ms(blueprint),
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
                blueprint.round_id,
                account.device_id,
                f"{blueprint.namespace}:order:{account.device_id}",
            )
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
    assignment_updates = (
        (
            "completed",
            1,
            completed_at_ms - 400,
            completed_at_ms,
            blueprint.round_id,
            identity_key,
            device_id,
        )
        for sequence, (identity_key, device_id) in enumerate(confirmed_keys)
        for completed_at_ms in (_completion_timestamp(blueprint, sequence),)
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
    created["profile_snapshots"] = _insert_profile_snapshots(connection, blueprint)
    action_counts = _insert_action_evidence(connection, blueprint, confirmed_keys)
    created.update(action_counts)
    created["device_health"] = _insert_device_health(connection, blueprint)
    created["operator_controls"] = _insert_operator_controls(connection, blueprint)
    return created


def _seed_conversion(
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> dict[str, int]:
    """Task 3 extension point kept inside the acquisition transaction."""
    del connection, blueprint
    return {}


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
            _completion_timestamp(blueprint, sequence)
            - _assignment_duration_ms(sequence),
            blueprint.round_id,
            identity_key,
            device_id,
        )
        for sequence, (identity_key, device_id) in enumerate(confirmed_keys)
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
    connection: sqlite3.Connection, blueprint: DemoBlueprint
) -> int:
    if not blueprint.accounts:
        return 0
    rows = (
        (
            blueprint.round_id,
            _target_identity(index),
            blueprint.accounts[(index - 1) % len(blueprint.accounts)].device_id,
            target_id,
            420 + index % 180,
            1_300 + index % 700,
            3 + index % 9,
            1,
            "demo_eligible",
            _demo_started_at_ms(blueprint) + index,
        )
        for index, target_id in enumerate(
            blueprint.targets[: blueprint.metrics.eligible], start=1
        )
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
) -> dict[str, int]:
    action_keys = confirmed_keys[: blueprint.metrics.interactions]
    uncertain_count = min(blueprint.metrics.ai_uncertain, len(action_keys))
    created = _executemany_bounded(
        connection,
        """
        INSERT OR IGNORE INTO device_action_plans(
            round_id, identity_key, device_id, seed, requested_outcome,
            effective_outcome, quota_window_start_ms, quota_reason, video_key,
            state, created_at_ms, policy_version
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            (
                blueprint.round_id,
                identity_key,
                device_id,
                f"{blueprint.namespace}:action:{sequence:05d}",
                outcome,
                outcome,
                "demo_capacity_evidence",
                f"demo-video-action-{sequence + 1:05d}",
                "uncertain" if sequence < uncertain_count else "confirmed",
                _completion_timestamp(blueprint, sequence) - 200,
                "demo-portfolio-v1",
            )
            for sequence, (identity_key, device_id) in enumerate(action_keys)
            for outcome in (_ACTION_OUTCOMES[sequence % len(_ACTION_OUTCOMES)],)
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
                "uncertain" if sequence < uncertain_count else "confirmed",
                json.dumps(
                    {"ui_summary": "DEMO synthetic visible-state evidence"},
                    separators=(",", ":"),
                ),
                _completion_timestamp(blueprint, sequence) - 100,
                blueprint.round_id,
                identity_key,
                device_id,
            )
            for sequence, (identity_key, device_id) in enumerate(action_keys)
        ),
    )
    return {
        "action_plans": created,
        "action_attempts": attempts_created,
        "uncertain_action_plans": (uncertain_count if created else 0),
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


def _take(rows: Iterable[tuple[str, str]], count: int) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for row in rows:
        if len(result) == count:
            break
        result.append(row)
    return result


def _demo_started_at_ms(blueprint: DemoBlueprint) -> int:
    return max(1, blueprint.now_ms - 13 * _DAY_MS)


def _completion_timestamp(blueprint: DemoBlueprint, sequence: int) -> int:
    span = 14 * _DAY_MS
    return max(1, blueprint.now_ms - span + (sequence * 7_919) % span)


def _assignment_duration_ms(sequence: int) -> int:
    return 4_100 + sequence % 2_200


def _target_identity(index: int) -> str:
    return f"{DEMO_POOL_IDENTITY_PREFIX}{index:05d}"


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
    )
    conversations: list[DemoConversation] = []
    for index in range(1, count + 1):
        language, stage, inbound_text, outbound_text = templates[
            (index - 1) % len(templates)
        ]
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
