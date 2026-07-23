import re
import threading
import time
from collections.abc import Callable

from .db import BrowserWelcomePlan, Database
from .web_accounts import WebAccount, WebAccountRegistry


_USERNAME = re.compile(r"^[a-z0-9._]+$")


def _normalized_username(value: str) -> str:
    username = str(value or "").strip().removeprefix("@").casefold()
    return username if _USERNAME.fullmatch(username) else ""


class BrowserWelcomeService:
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
        self._locks = {
            account.account_id: threading.Lock() for account in registry.accounts
        }

    def _account(self, account_id: str, device_id: str) -> WebAccount:
        try:
            account = self.registry.by_account_id(account_id)
        except KeyError as error:
            raise ValueError(str(error)) from error
        if account.device_id != device_id:
            raise ValueError("browser account and device mapping do not match")
        if not account.enabled or account.mode != "browser":
            raise ValueError("browser welcome messages are disabled for this account")
        return self.account_overlay(account)

    def plan_after_followback(
        self, account_id: str, device_id: str, follower_key: str
    ) -> BrowserWelcomePlan | None:
        account = self._account(account_id, device_id)
        if not account.welcome_after_followback:
            return None
        with self._locks[account.account_id]:
            raw_username = self.database.completed_followback_username(
                account.account_id, follower_key
            )
            username = _normalized_username(raw_username or "")
            if not username:
                return None
            if not self.database.browser_contact_allowed(account.account_id, username):
                return None
            existing = self.database.browser_welcome_plan(account.account_id, username)
            if existing is not None:
                return existing
            reply_text = self.reply_client.reply_conversation(
                [],
                offer_context=account.offer_context,
                faq_context=account.faq_text,
                conversation_stage="new",
                reply_tone=account.reply_tone,
                brand_name=account.brand_name,
                introduce_ai=True,
                response_mode="new_follower_welcome",
                welcome_language=account.welcome_language,
                fallback="",
                max_history_messages=1,
            )
            return self.database.create_browser_welcome_plan(
                account.account_id,
                username,
                follower_key,
                reply_text,
                now_ms=int(self.clock() * 1_000),
            )

    def next_plan(self, account_id: str, device_id: str) -> BrowserWelcomePlan | None:
        account = self._account(account_id, device_id)
        if not account.welcome_after_followback:
            return None
        return self.database.next_browser_welcome_plan(account.account_id)

    def record_result(
        self,
        account_id: str,
        device_id: str,
        plan_id: int,
        state: str,
    ) -> bool:
        account = self._account(account_id, device_id)
        return self.database.record_browser_welcome_result(
            account.account_id,
            int(plan_id),
            state,
            now_ms=int(self.clock() * 1_000),
        )
