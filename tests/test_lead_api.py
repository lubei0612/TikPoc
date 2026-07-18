import json
import sqlite3
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


def test_takeover_command_rejects_changed_reason_without_new_side_effects(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    route = "/api/leads/account-01/conversation-01/takeover"

    first = client.post(
        route, json={"command_id": "takeover-bound", "reason": "operator"}
    )
    conflict = client.post(
        route, json={"command_id": "takeover-bound", "reason": "manager"}
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM lead_funnel_events WHERE source_key=?",
                ("operator:takeover-bound",),
            ).fetchone()[0]
            == 1
        )


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


def test_return_to_ai_replays_stored_result_before_current_ai_switch(
    tmp_path: Path,
) -> None:
    app, _ = _seeded_app(tmp_path)
    client = TestClient(app)
    command = {"command_id": "return-before-ai-off"}

    first = client.post(
        "/api/leads/account-01/conversation-01/return-to-ai", json=command
    )
    disabled = client.post(
        "/api/accounts/account-01/ai-enable",
        json={"command_id": "disable-after-return", "enabled": False},
    )
    replay = client.post(
        "/api/leads/account-01/conversation-01/return-to-ai", json=command
    )
    new_command = client.post(
        "/api/leads/account-01/conversation-01/return-to-ai",
        json={"command_id": "return-after-ai-off"},
    )

    assert first.status_code == 200
    assert disabled.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert new_command.status_code == 409


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

    assert first.status_code == 200
    assert retry.status_code == 409
    plan = database.browser_reply_plan_by_id(first.json()["plan_id"])
    assert plan is not None
    assert plan.reply_text == "I will follow up personally."
    assert plan.state == "planned"


def test_manual_reply_command_rejects_changed_inbound_or_text(tmp_path: Path) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    assert (
        client.post(
            "/api/leads/account-01/conversation-01/takeover",
            json={"command_id": "takeover-manual-bound", "reason": "operator"},
        ).status_code
        == 200
    )
    route = "/api/leads/account-01/conversation-01/manual-reply-plan"
    body = {
        "command_id": "manual-bound",
        "inbound_fingerprint": "message-3",
        "reply_text": "Original manual reply.",
    }

    first = client.post(route, json=body)
    changed_text = client.post(route, json={**body, "reply_text": "Changed reply."})
    changed_inbound = client.post(
        route, json={**body, "inbound_fingerprint": "message-2"}
    )

    assert first.status_code == 200
    assert changed_text.status_code == changed_inbound.status_code == 409
    with sqlite3.connect(database.path) as connection:
        rows = connection.execute(
            "SELECT reply_text FROM browser_reply_plans WHERE plan_origin='manual'"
        ).fetchall()
    assert rows == [("Original manual reply.",)]


def test_manual_reply_plans_reuse_one_plan_for_same_inbound_across_commands(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    takeover = client.post(
        "/api/leads/account-01/conversation-01/takeover",
        json={"command_id": "takeover-canonical", "reason": "operator"},
    )
    assert takeover.status_code == 200

    first = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan",
        json={
            "command_id": "manual-canonical-1",
            "inbound_fingerprint": "message-1",
            "reply_text": "First immutable manual reply.",
        },
    )
    second = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan",
        json={
            "command_id": "manual-canonical-2",
            "inbound_fingerprint": "message-1",
            "reply_text": "A conflicting replacement.",
        },
    )

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert first.json()["inbound_fingerprint"] == "message-1"
    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM browser_reply_plans
            WHERE account_id=? AND conversation_id=? AND plan_origin='manual'
              AND source_inbound_fingerprint=?
            """,
                (
                    "account-01",
                    "conversation-01",
                    "message-1",
                ),
            ).fetchone()[0]
            == 1
        )
    plan = database.browser_reply_plan_by_id(first.json()["plan_id"])
    assert plan is not None
    assert plan.reply_text == "First immutable manual reply."
    assert plan.plan_origin == "manual"
    assert plan.source_inbound_fingerprint == "message-1"


def test_selected_lead_shows_manual_plan_for_source_inbound_fingerprint(
    tmp_path: Path,
) -> None:
    app, _ = _seeded_app(tmp_path)
    client = TestClient(app)
    assert (
        client.post(
            "/api/leads/account-01/conversation-01/takeover",
            json={"command_id": "takeover-selected-manual", "reason": "operator"},
        ).status_code
        == 200
    )
    created = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan",
        json={
            "command_id": "manual-selected",
            "inbound_fingerprint": "message-3",
            "reply_text": "Persistent operator reply.",
        },
    )
    assert created.status_code == 200

    selected = client.get(
        "/api/leads",
        params={
            "account_id": "account-01",
            "conversation_id": "conversation-01",
            "inbound_fingerprint": "message-3",
        },
    ).json()["selected"]

    assert selected["draft"] == {
        "plan_id": created.json()["plan_id"],
        "inbound_fingerprint": created.json()["inbound_fingerprint"],
        "reply_text": "Persistent operator reply.",
        "state": "planned",
    }


def test_ai_off_still_allows_taken_over_manual_plan_to_claim_send_lease(
    tmp_path: Path,
) -> None:
    app, _ = _seeded_app(tmp_path)
    client = TestClient(app)
    assert (
        client.post(
            "/api/accounts/account-01/ai-enable",
            json={"command_id": "ai-off-manual", "enabled": False},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/leads/account-01/conversation-01/takeover",
            json={"command_id": "takeover-ai-off", "reason": "operator"},
        ).status_code
        == 200
    )
    manual = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan",
        json={
            "command_id": "manual-ai-off",
            "inbound_fingerprint": "message-3",
            "reply_text": "I will handle this personally.",
        },
    )
    assert manual.status_code == 200
    claim = client.post(
        "/api/browser-actions/claim",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            "account_id": "account-01",
            "device_id": "phone-01",
            "action_type": "dm_send",
            "action_key": f"dm_send:{manual.json()['plan_id']}",
            "owner_id": "operator-tab",
            "timestamp_ms": 8_000,
        },
    )
    assert claim.status_code == 200
    assert claim.json() == {"claimed": True}


def test_takeover_supersedes_ai_draft_before_atomic_dm_claim(tmp_path: Path) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    draft = database.get_browser_reply_plan("account-01", "message-3")
    assert draft is not None

    assert (
        client.post(
            "/api/leads/account-01/conversation-01/takeover",
            json={"command_id": "takeover-before-claim", "reason": "operator"},
        ).status_code
        == 200
    )
    claim = client.post(
        "/api/browser-actions/claim",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            "account_id": "account-01",
            "device_id": "phone-01",
            "action_type": "dm_send",
            "action_key": f"dm_send:{draft.id}",
            "owner_id": "tab-01",
            "timestamp_ms": 8_000,
        },
    )

    assert claim.status_code == 200
    assert claim.json() == {"claimed": False}
    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM browser_action_leases").fetchone()[
                0
            ]
            == 0
        )


def test_dm_claim_rejects_plan_id_aliases_and_creates_only_canonical_lease(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    draft = database.get_browser_reply_plan("account-01", "message-3")
    assert draft is not None
    route = "/api/browser-actions/claim"
    base = {
        "account_id": "account-01",
        "device_id": "phone-01",
        "action_type": "dm_send",
        "owner_id": "tab-01",
        "timestamp_ms": 8_000,
    }

    for alias in (str(draft.id), f"dm_send:+{draft.id}", f"dm_send:0{draft.id}"):
        response = client.post(
            route,
            headers={"Origin": "https://www.tiktok.com"},
            json={**base, "action_key": alias},
        )
        assert response.status_code == 200
        assert response.json() == {"claimed": False}

    canonical = client.post(
        route,
        headers={"Origin": "https://www.tiktok.com"},
        json={**base, "action_key": f"dm_send:{draft.id}"},
    )
    assert canonical.status_code == 200
    assert canonical.json() == {"claimed": True}
    with sqlite3.connect(database.path) as connection:
        assert connection.execute(
            "SELECT action_key FROM browser_action_leases"
        ).fetchall() == [(f"dm_send:{draft.id}",)]


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
    changed_sale = client.post(
        "/api/leads/account-01/conversation-01/sale",
        json={
            "command_id": "sale-1",
            "amount_minor": 99_999,
            "currency": "USD",
            "status": "refunded",
            "occurred_at_ms": 8_000,
        },
    )
    assert changed_sale.status_code == 409

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


def test_account_setting_command_rejects_changed_enabled_flag(tmp_path: Path) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    route = "/api/accounts/account-01/ai-enable"

    first = client.post(route, json={"command_id": "ai-bound", "enabled": False})
    conflict = client.post(route, json={"command_id": "ai-bound", "enabled": True})

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert (
        database.account_operator_settings(
            "account-01", default_ai_enabled=True, default_followback_enabled=True
        )["ai_enabled"]
        is False
    )


def test_disabled_account_readiness_masks_persisted_operator_switches_and_blocks_claims(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    enabled_client = TestClient(app)
    assert (
        enabled_client.post(
            "/api/accounts/account-01/ai-enable",
            json={"command_id": "persist-ai-on", "enabled": True},
        ).status_code
        == 200
    )
    assert (
        enabled_client.post(
            "/api/accounts/account-01/followback-enable",
            json={"command_id": "persist-follow-on", "enabled": True},
        ).status_code
        == 200
    )

    account = app.state.registry.by_account_id("account-01")
    disabled_registry = WebAccountRegistry(
        (
            WebAccount(
                account_id=account.account_id,
                device_id=account.device_id,
                mode=account.mode,
                enabled=False,
                browser_dm_enabled=account.browser_dm_enabled,
                browser_followback_enabled=account.browser_followback_enabled,
                private_channel_hint=account.private_channel_hint,
                offer_context=account.offer_context,
                faq_text=account.faq_text,
            ),
            WebAccount(account_id="account-02", device_id="phone-02"),
        )
    )
    disabled_app = create_app(
        database.path, registry=disabled_registry, clock=lambda: 5
    )
    client = TestClient(disabled_app)

    readiness = client.get("/api/leads").json()["accounts"][0]
    assert readiness["enabled"] is False
    assert readiness["ai_enabled"] is False
    assert readiness["followback_enabled"] is False
    claim = client.post(
        "/api/browser-actions/claim",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            "account_id": "account-01",
            "device_id": "phone-01",
            "action_type": "followback",
            "action_key": "buyer-01",
            "owner_id": "disabled-tab",
            "timestamp_ms": 5_000,
        },
    )
    assert claim.status_code == 400
    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM browser_action_leases").fetchone()[
                0
            ]
            == 0
        )


def test_disabled_account_controls_reject_browser_action_claims_without_leases(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    headers = {"origin": "https://www.tiktok.com"}
    assert (
        client.post(
            "/api/accounts/account-01/followback-enable",
            json={"command_id": "followback-off", "enabled": False},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/accounts/account-01/ai-enable",
            json={"command_id": "ai-off", "enabled": False},
        ).status_code
        == 200
    )

    for action_type, action_key in (
        ("followback", "buyer-01"),
        ("dm_send", "plan-123"),
    ):
        response = client.post(
            "/api/browser-actions/claim",
            headers=headers,
            json={
                "account_id": "account-01",
                "device_id": "phone-01",
                "action_type": action_type,
                "action_key": action_key,
                "owner_id": "tab-01",
                "timestamp_ms": 5_000,
                "lease_seconds": 30,
            },
        )
        assert response.status_code == 200
        assert response.json() == {"claimed": False}

    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM browser_action_leases").fetchone()[
                0
            ]
            == 0
        )


def test_uncertain_send_blocks_manual_reply_plan_in_same_conversation(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    draft = database.get_browser_reply_plan("account-01", "message-3")
    assert draft is not None
    database.set_browser_reply_plan_state(draft.id, "uncertain")
    assert (
        client.post(
            "/api/leads/account-01/conversation-01/takeover",
            json={"command_id": "takeover-uncertain", "reason": "operator"},
        ).status_code
        == 200
    )

    response = client.post(
        "/api/leads/account-01/conversation-01/manual-reply-plan",
        json={
            "command_id": "manual-blocked",
            "inbound_fingerprint": "message-3",
            "reply_text": "Do not create this plan.",
        },
    )

    assert response.status_code == 409
    assert (
        database.get_browser_reply_plan(
            "account-01", "operator-manual:conversation-01:manual-blocked"
        )
        is None
    )


def test_lead_inbox_only_returns_registry_accounts_and_unconfigured_is_empty(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    database.append_web_message(
        "unknown-account",
        "unknown-conversation",
        "unknown-message",
        direction="inbound",
        message_type="TEXT",
        text="Secret destination: UNKNOWN_DESTINATION",
        timestamp_ms=9_000,
        participant_username="unknown-buyer",
    )

    configured = TestClient(app).get("/api/leads").json()
    assert configured["configured"] is True
    assert {item["account_id"] for item in configured["conversations"]} == {
        "account-01"
    }
    assert "UNKNOWN_DESTINATION" not in json.dumps(configured)

    unconfigured = TestClient(create_app(database.path)).get("/api/leads").json()
    assert unconfigured["configured"] is False
    assert unconfigured["accounts"] == []
    assert unconfigured["conversations"] == []
    assert unconfigured["selected"] is None
    assert unconfigured["funnel"] == {
        "followers": 0,
        "dm_inbound": 0,
        "engaged": 0,
        "qualified": 0,
        "invited": 0,
        "contact_captured": 0,
        "human_required": 0,
    }
    assert unconfigured["sales"] == {
        "by_status": {},
        "confirmed_revenue_minor": {},
        "sales": 0,
    }
    assert "UNKNOWN_DESTINATION" not in json.dumps(unconfigured)


def test_lead_analytics_only_include_registry_accounts(tmp_path: Path) -> None:
    app, database = _seeded_app(tmp_path)
    database.record_lead_funnel_event(
        "removed-account",
        "removed-buyer",
        "qualified",
        "removed-message",
        conversation_id="removed-conversation",
        occurred_at_ms=10_000,
    )
    database.record_lead_sale(
        "removed-account",
        "removed-buyer",
        amount_minor=99_999,
        currency="USD",
        status="confirmed",
        occurred_at_ms=10_000,
    )

    payload = TestClient(app).get("/api/leads").json()

    assert payload["funnel"]["qualified"] == 1
    assert payload["sales"] == {
        "by_status": {},
        "confirmed_revenue_minor": {},
        "sales": 0,
    }


def test_lead_summaries_include_persistent_flags_and_reply_timing(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    database.record_lead_funnel_event(
        "account-01",
        "buyer_01",
        "invited",
        "invitation-evidence",
        conversation_id="conversation-01",
        occurred_at_ms=3_500,
    )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations
            SET stage='human_required', contact_captured_at_ms=3600
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        )
    database.append_web_message(
        "account-01",
        "conversation-closed",
        "closed-inbound",
        direction="inbound",
        message_type="TEXT",
        text="closed thread",
        timestamp_ms=2_500,
        participant_username="buyer_02",
    )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations
            SET stage='closed', last_invited_at_ms=3500
            WHERE account_id='account-01' AND conversation_id='conversation-closed'
            """
        )

    conversations = TestClient(app).get("/api/leads").json()["conversations"]
    awaiting = next(
        item for item in conversations if item["conversation_id"] == "conversation-01"
    )
    closed = next(
        item
        for item in conversations
        if item["conversation_id"] == "conversation-closed"
    )

    assert awaiting["invitation_seen"] is True
    assert awaiting["contact_captured"] is True
    assert awaiting["last_message_direction"] == "inbound"
    assert awaiting["reply_wait_ms"] == 2_000
    assert awaiting["last_message_age_ms"] == 2_000
    assert closed["invitation_seen"] is True
    assert closed["contact_captured"] is True

    database.append_web_message(
        "account-01",
        "conversation-01",
        "outbound-after-reply",
        direction="outbound",
        message_type="TEXT",
        text="Synthetic response",
        timestamp_ms=4_000,
        participant_username="buyer_01",
    )

    replied = next(
        item
        for item in TestClient(app).get("/api/leads").json()["conversations"]
        if item["conversation_id"] == "conversation-01"
    )

    assert replied["last_message_direction"] == "outbound"
    assert replied["reply_wait_ms"] is None
    assert replied["last_message_age_ms"] == 1_000


def test_follower_measurement_is_distinct_and_registry_scoped(tmp_path: Path) -> None:
    app, database = _seeded_app(tmp_path)
    for account_id, conversation_id in (
        ("account-01", "conversation-01"),
        ("account-01", "conversation-duplicate"),
        ("account-02", "conversation-other-account"),
        ("removed-account", "conversation-removed"),
    ):
        database.append_web_message(
            account_id,
            conversation_id,
            f"message-{conversation_id}",
            direction="inbound",
            message_type="TEXT",
            text="hello",
            timestamp_ms=4_000,
            participant_username="same_buyer",
            is_follower=True,
        )

    payload = TestClient(app).get("/api/leads").json()

    assert payload["funnel"]["followers"] == 2

    empty_registry_payload = (
        TestClient(create_app(database.path)).get("/api/leads").json()
    )
    assert empty_registry_payload["funnel"]["followers"] == 0


def test_lead_redaction_handles_case_and_whitespace_variants_without_leaking_destination(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    database.append_web_message(
        "account-01",
        "conversation-01",
        "variant-message",
        direction="inbound",
        message_type="TEXT",
        text="Please use whatsapp:\t+1   555 0100",
        timestamp_ms=10_000,
        participant_username="buyer_01",
    )
    variant_plan, _ = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        "variant-message",
        "buyer_01",
        "Please use whatsapp:\t+1   555 0100",
        10_000,
    )
    database.complete_browser_reply_plan(
        variant_plan.id,
        reply_text="WHATSAPP: +1 555 0100 is fine",
        stage="qualified",
    )

    payload = (
        TestClient(app)
        .get(
            "/api/leads",
            params={
                "account_id": "account-01",
                "conversation_id": "conversation-01",
                "history_limit": 10,
                "inbound_fingerprint": "variant-message",
            },
        )
        .json()
    )
    preview = payload["conversations"][0]["last_message_preview"]
    history = payload["selected"]["messages"][-1]["text"]
    draft = payload["selected"]["draft"]["reply_text"]
    for exposed_text in (preview, history, draft):
        normalized = " ".join(exposed_text.split()).casefold()
        assert "whatsapp: +1 555 0100" not in normalized
        assert "[private channel configured]" in normalized


def test_readiness_requires_runtime_provider_base_url_key_and_model(
    tmp_path: Path, monkeypatch
) -> None:
    app, _ = _seeded_app(tmp_path)
    client = TestClient(app)
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "TIKPOC_AI_API_KEY",
        "TKAUTO_LLM_BASE_URL",
        "TKAUTO_LLM_API_KEY",
        "TKAUTO_LLM_MODEL",
        "MODEL_MONITOR_LLM_BASE_URL",
        "MODEL_MONITOR_LLM_API_KEY",
        "MODEL_MONITOR_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("OPENAI_API_KEY", "legacy-only")
    assert client.get("/api/leads").json()["accounts"][0]["model_configured"] is False

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TKAUTO_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("TKAUTO_LLM_API_KEY", "runtime-key")
    monkeypatch.setenv("TKAUTO_LLM_MODEL", "runtime-model")
    assert client.get("/api/leads").json()["accounts"][0]["model_configured"] is True

    monkeypatch.delenv("TKAUTO_LLM_MODEL", raising=False)
    assert client.get("/api/leads").json()["accounts"][0]["model_configured"] is False

    monkeypatch.delenv("TKAUTO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TKAUTO_LLM_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_MONITOR_LLM_BASE_URL", "https://monitor.example/v1")
    monkeypatch.setenv("MODEL_MONITOR_LLM_API_KEY", "monitor-key")
    monkeypatch.setenv("MODEL_MONITOR_LLM_MODEL", "monitor-model")
    assert client.get("/api/leads").json()["accounts"][0]["model_configured"] is True


def test_takeover_preserves_terminal_lead_stages_and_does_not_emit_closed_handoff(
    tmp_path: Path,
) -> None:
    app, database = _seeded_app(tmp_path)
    client = TestClient(app)
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations
            SET stage='closed', human_required=0
            WHERE account_id=? AND conversation_id=?
            """,
            ("account-01", "conversation-01"),
        )

    response = client.post(
        "/api/leads/account-01/conversation-01/takeover",
        json={"command_id": "takeover-closed", "reason": "operator"},
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "closed"
    assert response.json()["human_required"] is False
    assert (
        database.browser_conversation_state("account-01", "conversation-01").stage
        == "closed"
    )
    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM lead_funnel_events
            WHERE account_id=? AND conversation_id=? AND stage='human_required'
            """,
                ("account-01", "conversation-01"),
            ).fetchone()[0]
            == 0
        )
