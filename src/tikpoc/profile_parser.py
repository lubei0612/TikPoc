from dataclasses import dataclass
from xml.etree import ElementTree

from .counts import parse_visible_count
from .models import ProfileMetrics


@dataclass(frozen=True)
class ProfilePage:
    username: str
    metrics: ProfileMetrics
    visible_post_count: int


def parse_profile_page(page_source: str) -> ProfilePage:
    root = ElementTree.fromstring(page_source)
    username = ""
    stats: dict[str, int] = {}
    pending_value: str | None = None
    visible_post_count = 0

    for node in root.iter("node"):
        resource_id = node.attrib.get("resource-id", "")
        text = node.attrib.get("text", "").strip()
        if resource_id.endswith(":id/s7e"):
            username = text.removeprefix("@").lower()
        elif resource_id.endswith(":id/s5y"):
            pending_value = text
        elif resource_id.endswith(":id/s5x") and pending_value is not None:
            stats[text.lower()] = parse_visible_count(pending_value)
            pending_value = None
        elif resource_id.endswith(":id/cover"):
            visible_post_count += 1

    if not username or "following" not in stats or "followers" not in stats:
        raise ValueError("profile metrics are incomplete")
    return ProfilePage(
        username=username,
        metrics=ProfileMetrics(
            following=stats["following"],
            followers=stats["followers"],
            posts=visible_post_count,
        ),
        visible_post_count=visible_post_count,
    )
