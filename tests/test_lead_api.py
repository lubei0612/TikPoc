import json
from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.api import create_app
from tikpoc.browser_dm import BrowserDmService
from tikpoc.db import Database
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


class _ReplyClient:
    def reply_conversation(self, *_args, **_kwargs) -> str:
        return "Synthetic reply"


def _seeded_app(tmp_path: Path):
    path = tmp_path / "tikpoc.db"
    database = Database(path)
    database.migrate()
    registry = WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                private_channel_hint="WhatsApp: +1 555 0100",
                offer_context="Synthetic offer",
                faq_text="Synthetic FAQ",
            ),
            WebAccount(account_id="account-02", device_id="phone-02"),
        )
    )
    for index, text in enumerate(("first", "second", "x" * 220), start=1):
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"message-{index}",
            direction="inbound",
            message_type="TEXT",
            text=text,
            timestamp_ms=index * 1_000,
            participant_username="buyer_01",
        )
    database.record_lead_funnel_event(
        "account-01",
        "buyer_01",
        "qualified",
        "message-3",
        conversation_id="conversation-01",
        occurred_at_ms=3_000,
    )
    draft, _ = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        "message-3",
        "buyer_01",
        "x" * 220,
        3_000,
    )
    database.complete_browser_reply_plan(
        draft.id,
        reply_text="Continue at WhatsApp: +1 555 0100",
        stage="qualified",
    )
    service = BrowserDmService(database, registry, _ReplyClient(), clock=lambda: 5)
    return create_app(
        path,
        registry=registry,
        browser_dm_service=service,
        clock=lambda: 5,
    ), database


def test_lead_list_redacts_secrets_and_returns_readiness_and_selected_history(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-private-value")
    app, _ = _seeded_app(tmp_path)

    response = TestClient(app).get(
        "/api/leads",
        params={
            "limit": 20,
            "account_id": "account-01",
            "conversation_id": "conversation-01",
            "history_limit": 2,
            "inbound_fingerprint": "message-3",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    account = payload["accounts"][0]
    assert account["private_channel_configured"] is True
    assert account["ai_enabled"] is True
    assert account["followback_enabled"] is True
    assert set(payload["conversations"][0]) >= {
        "stage",
        "participant_username",
        "last_message_preview",
        "human_required",
    }
    assert len(payload["conversations"][0]["last_message_preview"]) == 160
    assert [message["message_id"] for message in payload["selected"]["messages"]] == [
        "message-2",
        "message-3",
    ]
    assert payload["funnel"]["qualified"] == 1
    assert payload["sales"]["sales"] == 0
    serialized = json.dumps(payload)
    assert "555 0100" not in serialized
    assert "sk-private-value" not in serialized


def test_takeover_is_idempotent_and_disables_future_ai_plans(tmp_path: Path) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    request = {"command_id": "takeover-1", "reason": "operator"}

    first = client.post("/api/leads/account-01/conversation-01/takeover", json=request)
    second = client.post("/api/leads/account-01/conversation-01/takeover", json=request)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["stage"] == "human_required"
    assert database.browser_conversation_state(
        "account-01", "conversation-01"
    ).human_required
    blocked = client.post(
        "/api/browser-dm/reply-plan",
        headers={"origin": "https://www.tiktok.com"},
        json={
            "account_id": "account-01",
            "device_id": "phone-01",
            "conversation_id": "conversation-01",
            "fingerprint": "future-message",
            "participant_username": "buyer_01",
            "text": "hello again",
            "timestamp_ms": 6_000,
        },
    )
    assert blocked.status_code == 409
    assert database.get_browser_reply_plan("account-01", "future-message") is None


def test_return_to_ai_requires_nonterminal_state_and_no_uncertain_send(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)

    accepted = client.post(
        "/api/leads/account-01/conversation-01/return-to-ai",
        json={"command_id": "return-1"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["ai_enabled"] is True

    plan, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "uncertain-fp", "buyer_01", "hello", 4_000
    )
    database.complete_browser_reply_plan(
        plan.id, reply_text="pending reply", stage="qualified"
    )
    database.set_browser_reply_plan_state(plan.id, "uncertain")
    blocked = client.post(
        "/api/leads/account-01/conversation-01/return-to-ai",
        json={"command_id": "return-2"},
    )
    assert blocked.status_code == 409

    takeover = client.post(
        "/api/leads/account-01/conversation-01/takeover",
        json={"command_id": "takeover-terminal", "reason": "operator"},
    )
    assert takeover.status_code == 200
    terminal = client.post(
        "/api/leads/account-01/conversation-01/return-to-ai",
        json={"command_id": "return-terminal"},
    )
    assert terminal.status_code == 409


def test_manual_reply_plan_is_immutable_and_uses_normal_send_lease_path(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    body = {
        "command_id": "manual-1",
        "inbound_fingerprint": "message-3",
        "reply_text": "I will follow up personally.",
    }
    takeover = client.post(
        "/api/leads/account-01/conversation-01/takeover",
        json={"command_id": "takeover-manual", "reason": "operator"},
    )
    assert takeover.status_code == 200

    first = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan", json=body
    )
    retry = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan",
        json={**body, "reply_text": "replacement must not win"},
    )

    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    plan = database.browser_reply_plan_by_id(first.json()["plan_id"])
    assert plan is not None
    assert plan.reply_text == "I will follow up personally."
    assert plan.state == "planned"
    assert database.claim_browser_action(
        "account-01", "dm_send", str(plan.id), "operator-tab", 5_000, 30
    )


def test_sale_uses_minor_units_and_account_switches_persist(tmp_path: Path) -> None:
    app, _ = _seeded_app(tmp_path)
    client = TestClient(app)

    sale = client.post(
        "/api/leads/account-01/conversation-01/sale",
        json={
            "command_id": "sale-1",
            "amount_minor": 12_345,
            "currency": "USD",
            "status": "confirmed",
            "occurred_at_ms": 7_000,
        },
    )
    assert sale.status_code == 200
    assert sale.json()["amount_minor"] == 12_345
    assert (
        client.post(
            "/api/leads/account-01/conversation-01/sale",
            json={
                "command_id": "sale-1",
                "amount_minor": 99_999,
                "currency": "USD",
                "status": "confirmed",
                "occurred_at_ms": 8_000,
            },
        ).json()
        == sale.json()
    )

    assert (
        client.post(
            "/api/accounts/account-01/ai-enable",
            json={"command_id": "ai-off", "enabled": False},
        ).json()["ai_enabled"]
        is False
    )
    assert (
        client.post(
            "/api/accounts/account-01/followback-enable",
            json={"command_id": "follow-off", "enabled": False},
        ).json()["followback_enabled"]
        is False
    )
    readiness = client.get("/api/leads").json()["accounts"][0]
    assert readiness["ai_enabled"] is False
    assert readiness["followback_enabled"] is False
    assert client.get("/api/leads").json()["sales"] == {
        "by_status": {"confirmed": 1},
        "confirmed_revenue_minor": {"USD": 12_345},
        "sales": 1,
    }
