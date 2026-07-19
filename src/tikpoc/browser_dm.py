import threading
import time
from dataclasses import dataclass
from typing import Callable

from .db import BrowserConversationBusy, BrowserReplyPlan, Database
from .lead_conversion import (
    ConversationStage,
    assess_inbound,
    preferred_private_channel,
)
from .web_accounts import WebAccount, WebAccountRegistry

__all__ = (
    "BrowserConversationBusy",
    "BrowserDmService",
    "BrowserInbound",
    "BrowserReply",
)


@dataclass(frozen=True)
class BrowserInbound:
    account_id: str
    device_id: str
    conversation_id: str
    fingerprint: str
    participant_username: str
    text: str
    timestamp_ms: int


@dataclass(frozen=True)
class BrowserReply:
    plan_id: int
    conversation_id: str
    inbound_fingerprint: str
    reply_text: str
    stage: str


def _reply_from_plan(plan: BrowserReplyPlan) -> BrowserReply:
    return BrowserReply(
        plan_id=plan.id,
        conversation_id=plan.conversation_id,
        inbound_fingerprint=plan.inbound_fingerprint,
        reply_text=plan.reply_text,
        stage=plan.stage,
    )


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


class BrowserDmService:
    def __init__(
        self,
        database: Database,
        registry: WebAccountRegistry,
        reply_client,
        *,
        clock: Callable[[], float] = time.time,
        account_overlay: Callable[[WebAccount], WebAccount] | None = None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.reply_client = reply_client
        self.clock = clock
        self.account_overlay = account_overlay or (lambda account: account)
        self._account_locks = {
            account.account_id: threading.Lock() for account in registry.accounts
        }

    def _account(
        self, account_id: str, device_id: str, *, require_dm_enabled: bool = True
    ) -> WebAccount:
        try:
            account = self.registry.by_account_id(account_id)
        except KeyError as error:
            raise ValueError(str(error)) from error
        if account.device_id != device_id:
            raise ValueError("browser account and device mapping do not match")
        if require_dm_enabled and (
            not account.enabled
            or account.mode != "browser"
            or not account.browser_dm_enabled
        ):
            raise ValueError("browser direct messages are disabled for this account")
        return self.account_overlay(account)

    @staticmethod
    def _private_channel_selection(
        account: WebAccount, inbound_text: str, *, invitation_due: bool
    ) -> tuple[str, bool]:
        if not invitation_due:
            return "", False
        channels = {
            "whatsapp": _normalize_whitespace(account.whatsapp),
            "telegram": _normalize_whitespace(account.telegram),
        }
        channels = {name: value for name, value in channels.items() if value}
        if not channels:
            return _normalize_whitespace(account.private_channel_hint), False
        preferred = preferred_private_channel(inbound_text)
        if preferred in channels:
            return channels[preferred], False
        if len(channels) == 1:
            return next(iter(channels.values())), False
        return "", True

    def plan(self, inbound: BrowserInbound) -> BrowserReply:
        values = (
            inbound.account_id,
            inbound.device_id,
            inbound.conversation_id,
            inbound.fingerprint,
            inbound.participant_username,
            inbound.text,
        )
        if any(not str(value).strip() for value in values):
            raise ValueError("browser inbound fields must be nonempty")
        if int(inbound.timestamp_ms) < 0:
            raise ValueError("browser inbound timestamp must be nonnegative")
        account = self._account(inbound.account_id, inbound.device_id)
        lock = self._account_locks[account.account_id]
        with lock:
            self.database.record_lead_funnel_event(
                account.account_id,
                inbound.participant_username,
                "dm_inbound",
                inbound.fingerprint,
                conversation_id=inbound.conversation_id,
                occurred_at_ms=inbound.timestamp_ms,
            )
            existing = self.database.get_browser_reply_plan(
                account.account_id, inbound.fingerprint
            )
            if existing is not None and existing.state != "planning":
                return _reply_from_plan(existing)

            plan, _ = self.database.reserve_browser_inbound_plan(
                account.account_id,
                inbound.conversation_id,
                inbound.fingerprint,
                inbound.participant_username,
                inbound.text,
                inbound.timestamp_ms,
            )
            if plan.state != "planning":
                return _reply_from_plan(plan)

            state = self.database.browser_conversation_state(
                account.account_id, plan.conversation_id
            )
            now_ms = int(self.clock() * 1_000)
            assessment = assess_inbound(
                ConversationStage(state.stage),
                plan.inbound_text,
                state.meaningful_turns,
                account.invite_after_meaningful_turns,
                state.last_invited_at_ms,
                now_ms,
            )
            if assessment.meaningful:
                self.database.record_lead_funnel_event(
                    account.account_id,
                    plan.participant_username,
                    "engaged",
                    plan.inbound_fingerprint,
                    conversation_id=plan.conversation_id,
                    occurred_at_ms=now_ms,
                )
            if assessment.stage == ConversationStage.QUALIFIED:
                self.database.record_lead_funnel_event(
                    account.account_id,
                    plan.participant_username,
                    "qualified",
                    plan.inbound_fingerprint,
                    conversation_id=plan.conversation_id,
                    occurred_at_ms=now_ms,
                )
            if assessment.stage in {
                ConversationStage.CONTACT_CAPTURED,
                ConversationStage.HUMAN_REQUIRED,
            }:
                self.database.record_lead_funnel_event(
                    account.account_id,
                    plan.participant_username,
                    assessment.stage.value,
                    plan.inbound_fingerprint,
                    conversation_id=plan.conversation_id,
                    occurred_at_ms=now_ms,
                )
            if assessment.stage in {
                ConversationStage.CLOSED,
                ConversationStage.HUMAN_REQUIRED,
            }:
                completed = self.database.finalize_browser_reply_plan(
                    plan.id,
                    reply_text="",
                    plan_stage=assessment.stage.value,
                    conversation_stage=assessment.stage.value,
                    meaningful=assessment.meaningful,
                    now_ms=now_ms,
                    max_auto_replies=account.max_auto_replies,
                    contact_captured=(
                        assessment.stage == ConversationStage.CONTACT_CAPTURED
                    ),
                )
                return _reply_from_plan(completed)

            private_channel_hint, ask_channel_preference = (
                self._private_channel_selection(
                    account,
                    plan.inbound_text,
                    invitation_due=assessment.should_invite,
                )
            )
            configured_invite = bool(assessment.should_invite and private_channel_hint)
            if (
                assessment.should_invite
                and not configured_invite
                and not ask_channel_preference
            ):
                self.database.record_browser_diagnostic_event(
                    account.account_id,
                    "invite_configuration_missing",
                    plan.inbound_fingerprint,
                    {"conversation_id": plan.conversation_id},
                )

            confirmed_replies, reserved_replies = (
                self.database.browser_reply_budget_counts(
                    account.account_id,
                    plan.conversation_id,
                    excluding_plan_id=plan.id,
                )
            )
            if confirmed_replies + reserved_replies >= account.max_auto_replies:
                conversation_stage = (
                    ConversationStage.CLOSED
                    if confirmed_replies >= account.max_auto_replies
                    else assessment.stage
                )
                completed = self.database.finalize_browser_reply_plan(
                    plan.id,
                    reply_text="",
                    plan_stage=ConversationStage.CLOSED.value,
                    conversation_stage=conversation_stage.value,
                    meaningful=assessment.meaningful,
                    now_ms=now_ms,
                    max_auto_replies=account.max_auto_replies,
                    contact_captured=(
                        assessment.stage == ConversationStage.CONTACT_CAPTURED
                    ),
                )
                return _reply_from_plan(completed)

            history = self.database.recent_web_messages(
                account.account_id, plan.conversation_id, limit=12
            )
            reply_text = self.reply_client.reply_conversation(
                history,
                private_channel_hint=private_channel_hint,
                offer_context=account.offer_context,
                faq_context=account.faq_text,
                conversation_stage=assessment.stage.value,
                should_invite=configured_invite,
                ask_private_channel_preference=ask_channel_preference,
                reply_tone=account.reply_tone,
                fallback=account.fallback_acknowledgement,
                max_history_messages=12,
            )
            stage = assessment.stage
            invitation_included = bool(
                configured_invite
                and private_channel_hint in _normalize_whitespace(reply_text)
            )
            if invitation_included:
                stage = ConversationStage.INVITED
            completed = self.database.finalize_browser_reply_plan(
                plan.id,
                reply_text=reply_text,
                plan_stage=stage.value,
                conversation_stage=assessment.stage.value,
                meaningful=assessment.meaningful,
                now_ms=now_ms,
                max_auto_replies=account.max_auto_replies,
                contact_captured=(
                    assessment.stage == ConversationStage.CONTACT_CAPTURED
                ),
                invitation_included=invitation_included,
            )
            return _reply_from_plan(completed)

    def record_result(
        self,
        account_id: str,
        device_id: str,
        plan_id: int,
        state: str,
    ) -> bool:
        if state not in {"sent", "uncertain", "superseded"}:
            raise ValueError(f"invalid browser reply result state: {state}")
        account = self._account(account_id, device_id, require_dm_enabled=False)
        lock = self._account_locks[account.account_id]
        with lock:
            plan = self.database.browser_reply_plan_by_id(int(plan_id))
            if plan is None:
                raise KeyError(plan_id)
            if plan.account_id != account.account_id:
                raise ValueError("browser reply plan belongs to a different account")
            migration_hints = (
                account.private_channel_hint,
                account.whatsapp,
                account.telegram,
            )
            normalized_reply = _normalize_whitespace(plan.reply_text)
            migration_hint = next(
                (
                    _normalize_whitespace(hint)
                    for hint in migration_hints
                    if _normalize_whitespace(hint)
                    and _normalize_whitespace(hint) in normalized_reply
                ),
                "",
            )
            reconciled = self.database.reconcile_browser_reply_invitation_evidence(
                account.account_id,
                plan.id,
                private_channel_hint=migration_hint,
            )
            now_ms = int(self.clock() * 1_000)
            recorded = self.database.record_browser_reply_result(
                account.account_id,
                plan.id,
                state,
                now_ms=now_ms,
            )
            if (
                recorded
                and state == "sent"
                and reconciled.invitation_included
                and reconciled.participant_username.strip()
            ):
                self.database.record_lead_funnel_event(
                    account.account_id,
                    reconciled.participant_username,
                    "invited",
                    reconciled.inbound_fingerprint,
                    conversation_id=reconciled.conversation_id,
                    occurred_at_ms=now_ms,
                )
            return recorded
