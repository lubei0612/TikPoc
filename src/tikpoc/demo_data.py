import random
from dataclasses import dataclass
from datetime import UTC, datetime

DEMO_NAMESPACE = "demo-ai-growth-v1"
DEMO_POOL_ID = "demo-pool-ai-growth-v1"
DEMO_ROUND_ID = "demo-round-ai-growth-v1"
DEMO_POOL_IDENTITY_PREFIX = "demo:target:"
DEMO_ROUND_LABEL = "DEMO · AI 多账号获客转化试点"
DEMO_SEED = 20260904
_DAY_MS = 86_400_000


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


def build_demo_blueprint(
    *,
    now_ms: int,
    scale: DemoScale | None = None,
) -> DemoBlueprint:
    selected = scale or DemoScale.portfolio()
    if now_ms <= 0:
        raise ValueError("demo clock must be positive")
    return _build_blueprint(selected, now_ms=now_ms, seed=DEMO_SEED)


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
