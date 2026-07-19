from pathlib import Path

from tikpoc.browser_welcome import BrowserWelcomeService
from tikpoc.db import Database
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


class FakeReplyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, object]], dict[str, object]]] = []

    def reply_conversation(
        self, history: list[dict[str, object]], **kwargs: object
    ) -> str:
        self.calls.append((history, kwargs))
        return "Synthetic welcome draft"


def _registry() -> WebAccountRegistry:
    return WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                mode="browser",
                brand_name="Sample Brand",
                welcome_after_followback=True,
                welcome_language="English",
            ),
            WebAccount(
                account_id="account-02",
                device_id="phone-02",
                mode="browser",
                brand_name="Second Brand",
                welcome_after_followback=True,
                welcome_language="French",
            ),
        )
    )


def _record_followback(
    database: Database,
    account_id: str,
    follower_key: str,
    username: str,
    *,
    state: str = "completed",
) -> None:
    database.enqueue_web_event(
        account_id,
        "followback_completed",
        follower_key,
        {"username": username},
    )
    assert database.claim_browser_action(
        account_id,
        "followback",
        follower_key,
        "activity-tab",
        1_000,
    )
    assert database.finish_browser_action(
        account_id,
        "followback",
        follower_key,
        "activity-tab",
        state,
    )


def test_completed_followback_creates_one_durable_welcome_plan(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    _record_followback(database, "account-01", "follower:key-1", "Buyer.One")
    ai = FakeReplyClient()
    service = BrowserWelcomeService(database, _registry(), ai, clock=lambda: 2.0)

    first = service.plan_after_followback("account-01", "phone-01", "follower:key-1")
    second = service.plan_after_followback("account-01", "phone-01", "follower:key-1")

    assert first is not None
    assert second == first
    assert first.follower_username == "buyer.one"
    assert first.reply_text == "Synthetic welcome draft"
    assert first.state == "planned"
    assert len(ai.calls) == 1
    assert ai.calls[0][0] == []
    assert ai.calls[0][1] == {
        "offer_context": "",
        "faq_context": "",
        "conversation_stage": "new",
        "reply_tone": "",
        "brand_name": "Sample Brand",
        "introduce_ai": True,
        "response_mode": "new_follower_welcome",
        "welcome_language": "English",
        "fallback": "",
        "max_history_messages": 1,
    }


def test_uncertain_or_unmatched_followback_creates_no_welcome(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    _record_followback(
        database,
        "account-01",
        "follower:uncertain",
        "buyer",
        state="uncertain",
    )
    service = BrowserWelcomeService(database, _registry(), FakeReplyClient())

    assert (
        service.plan_after_followback("account-01", "phone-01", "follower:uncertain")
        is None
    )
    assert (
        service.plan_after_followback("account-01", "phone-01", "follower:missing")
        is None
    )


def test_repeat_follow_events_deduplicate_by_normalized_username(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    _record_followback(database, "account-01", "follower:key-1", "Buyer.One")
    _record_followback(database, "account-01", "follower:key-2", "@buyer.one")
    ai = FakeReplyClient()
    service = BrowserWelcomeService(database, _registry(), ai)

    first = service.plan_after_followback("account-01", "phone-01", "follower:key-1")
    second = service.plan_after_followback("account-01", "phone-01", "follower:key-2")

    assert first is not None and second is not None
    assert first.id == second.id
    assert len(ai.calls) == 1


def test_equal_follower_usernames_remain_isolated_between_accounts(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    _record_followback(database, "account-01", "follower:key", "same.user")
    _record_followback(database, "account-02", "follower:key", "same.user")
    service = BrowserWelcomeService(database, _registry(), FakeReplyClient())

    first = service.plan_after_followback("account-01", "phone-01", "follower:key")
    second = service.plan_after_followback("account-02", "phone-02", "follower:key")

    assert first is not None and second is not None
    assert first.id != second.id
    assert first.account_id == "account-01"
    assert second.account_id == "account-02"


def test_welcome_send_lease_requires_matching_planned_account_plan(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    _record_followback(database, "account-01", "follower:key", "buyer")
    service = BrowserWelcomeService(database, _registry(), FakeReplyClient())
    plan = service.plan_after_followback("account-01", "phone-01", "follower:key")
    assert plan is not None

    assert database.claim_browser_welcome_action(
        "account-01", f"welcome_send:{plan.id}", "messages-tab", 2_000
    )
    assert not database.claim_browser_welcome_action(
        "account-01", f"welcome_send:{plan.id}", "other-tab", 2_001
    )
    assert not database.claim_browser_welcome_action(
        "account-02", f"welcome_send:{plan.id}", "messages-tab", 2_000
    )
    assert not database.claim_browser_welcome_action(
        "account-01", "welcome_send:9999", "messages-tab", 2_000
    )
