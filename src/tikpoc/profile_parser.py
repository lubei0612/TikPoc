from dataclasses import dataclass
from xml.etree import ElementTree

from .counts import parse_visible_count
from .models import ProfileMetrics


@dataclass(frozen=True)
class ProfilePage:
    username: str
    metrics: ProfileMetrics
    visible_post_count: int
    visible_post_keys: tuple[str, ...]


def parse_visible_post_keys(page_source: str) -> tuple[str, ...]:
    root = ElementTree.fromstring(page_source)
    return tuple(
        node.attrib.get("text", "").strip()
        for node in root.iter()
        if node.attrib.get("resource-id", "").endswith((":id/tv_play_count", ":id/z9h"))
        and node.attrib.get("text", "").strip()
    )


def parse_profile_username(page_source: str) -> str:
    root = ElementTree.fromstring(page_source)
    for node in root.iter():
        resource_id = node.attrib.get("resource-id", "")
        if resource_id.endswith((":id/s7e", ":id/rgn")):
            return node.attrib.get("text", "").strip().removeprefix("@").lower()
    return ""


def profile_surface_visible(page_source: str) -> bool:
    root = ElementTree.fromstring(page_source)
    for node in root.iter():
        text = node.attrib.get("text", "").strip()
        description = node.attrib.get("content-desc", "").strip()
        resource_id = node.attrib.get("resource-id", "")
        if (
            "this account is private" in text.lower()
            or "this account is private" in description.lower()
            or "此帐户为私密帐户" in text
            or "此帐户为私密帐户" in description
            or resource_id.endswith((":id/s5x", ":id/rfc"))
        ):
            return True
    return False


def parse_profile_page(page_source: str) -> ProfilePage:
    root = ElementTree.fromstring(page_source)
    username = parse_profile_username(page_source)
    stats: dict[str, int] = {}
    pending_value: str | None = None
    visible_post_count = 0
    visible_post_keys = parse_visible_post_keys(page_source)

    for node in root.iter():
        resource_id = node.attrib.get("resource-id", "")
        text = node.attrib.get("text", "").strip()
        if resource_id.endswith((":id/s5y", ":id/rfd")):
            pending_value = text
        elif resource_id.endswith((":id/s5x", ":id/rfc")) and pending_value is not None:
            label = text.lower()
            if label == "follower":
                label = "followers"
            stats[label] = parse_visible_count(pending_value)
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
        visible_post_keys=visible_post_keys,
    )
