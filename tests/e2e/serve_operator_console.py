import shutil
import sqlite3
from pathlib import Path

import uvicorn

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.api import create_app
from tikpoc.browser_dm import BrowserDmService
from tikpoc.db import Database
from tikpoc.importer import Target
from tikpoc.rounds import create_exposure_round
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


class SyntheticReplyClient:
    def reply_conversation(self, *_args, **_kwargs) -> str:
        return "Synthetic reply"


output = Path("test-results/operator-console-seed")
shutil.rmtree(output, ignore_errors=True)
output.mkdir(parents=True)
database_path = output / "tikpoc.db"

identity = "sec:" + "long-synthetic-identity-" * 10
targets = (
    Target(
        target_id="lead-one",
        username="long_target_identity_for_mobile_layout_verification",
        profile_url="https://www.tiktok.com/@synthetic_one",
        source_video_id="video-one",
        sec_uid=identity.removeprefix("sec:"),
        identity_key=identity,
        source_line_numbers=(2,),
    ),
    Target(
        target_id="lead-two",
        username="synthetic_two",
        profile_url="https://www.tiktok.com/@synthetic_two",
        source_video_id="video-two",
        sec_uid="synthetic-two",
        identity_key="sec:synthetic-two",
        source_line_numbers=(3,),
    ),
)
repository = AcquisitionRepository(database_path, clock_ms=lambda: 12_000)
repository.migrate()
pool = repository.import_pool("synthetic.csv", "e" * 64, targets)
round_id = create_exposure_round(
    repository,
    pool_id=pool.pool_id,
    device_seeds={
        "phone-01-long-identity": "seed-01",
        "phone-02": "seed-02",
        "phone-03": "seed-03",
    },
    starts_at_ms=1_000,
    min_inter_device_gap_ms=0,
    min_repeat_gap_ms=0,
)
for index, device_id in enumerate(repository.round_device_ids(round_id), start=1):
    repository.record_fleet_device_health(
        device_id,
        f"account-{index:02d}",
        "healthy",
        now_ms=11_000,
        fence_token=index,
    )

with sqlite3.connect(database_path) as connection:
    rows = connection.execute(
        "SELECT assignment_id FROM round_assignments ORDER BY assignment_id"
    ).fetchall()
    for index, (assignment_id,) in enumerate(rows):
        if index < 3:
            connection.execute(
                """
                UPDATE round_assignments
                SET phase='completed', visit_confirmed_at_ms=?, completed_at_ms=?
                WHERE assignment_id=?
                """,
                (2_000 + index * 100, 2_500 + index * 100, assignment_id),
            )
        elif index == len(rows) - 1:
            connection.execute(
                """
                UPDATE round_assignments
                SET phase='deferred', attempt_count=2,
                    next_attempt_at_ms=13000, last_error_code='selector_missing'
                WHERE assignment_id=?
                """,
                (assignment_id,),
            )

database = Database(database_path)
database.migrate()
database.upsert_browser_health(
    "account-01",
    "messages",
    device_id="phone-01-long-identity",
    status="ready",
    observed_at_ms=11_500,
    detail="synthetic ready",
    observed_username="synthetic_shop_account_with_a_long_username",
)
database.upsert_browser_health(
    "account-01",
    "activity",
    device_id="phone-01-long-identity",
    status="ready",
    observed_at_ms=11_500,
    detail="synthetic ready",
    observed_username="synthetic_shop_account_with_a_long_username",
)
participant = "buyer_with_a_very_long_synthetic_username_for_overflow_testing"
for index, text in enumerate(("Interested", "Please share the details"), start=1):
    database.append_web_message(
        "account-01",
        "conversation-01",
        f"message-{index}",
        direction="inbound",
        message_type="TEXT",
        text=text,
        timestamp_ms=index * 1_000,
        participant_username=participant,
    )
database.record_lead_funnel_event(
    "account-01",
    participant,
    "qualified",
    "message-2",
    conversation_id="conversation-01",
    occurred_at_ms=2_000,
)
registry = WebAccountRegistry(
    (
        WebAccount(
            account_id="account-01",
            device_id="phone-01-long-identity",
            private_channel_hint="SYNTHETIC_PRIVATE_DESTINATION",
            offer_context="Synthetic offer",
            faq_text="Synthetic FAQ",
            expected_tiktok_username="synthetic_shop_account_with_a_long_username",
            browser_profile_label="客服一号专用 Chrome Profile（超长名称布局检查）",
        ),
        WebAccount(
            account_id="account-02",
            device_id="phone-02",
            expected_tiktok_username="synthetic_shop_two",
            browser_profile_label="客服二号 Chrome Profile",
        ),
    )
)
app = create_app(
    database_path,
    registry=registry,
    browser_dm_service=BrowserDmService(
        database, registry, SyntheticReplyClient(), clock=lambda: 12
    ),
    clock=lambda: 12,
)

uvicorn.run(app, host="127.0.0.1", port=8876, log_level="warning")
