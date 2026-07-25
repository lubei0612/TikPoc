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
        if node.attrib.get("resource-id", "").endswith(
            (":id/tv_play_count", ":id/z9h", ":id/vlr")
        )
        and node.attrib.get("text", "").strip()
    )


def parse_profile_username(page_source: str) -> str:
    root = ElementTree.fromstring(page_source)
    for node in root.iter():
        resource_id = node.attrib.get("resource-id", "")
        if resource_id.endswith((":id/s7e", ":id/rgn", ":id/oul")):
            return node.attrib.get("text", "").strip().removeprefix("@").lower()
    return ""


def private_profile_visible(page_source: str) -> bool:
    root = ElementTree.fromstring(page_source)
    for node in root.iter():
        visible_text = " ".join(
            (
                node.attrib.get("text", "").strip(),
                node.attrib.get("content-desc", "").strip(),
            )
        ).lower()
        if (
            "this account is private" in visible_text
            or "follow this account to see their videos" in visible_text
            or "此帐户为私密帐户" in visible_text
            or "关注此账号，即可查看对方的作品和点赞的作品" in visible_text
        ):
            return True
    return False


def profile_recommendations_visible(page_source: str) -> bool:
    root = ElementTree.fromstring(page_source)
    markers = {"recommended accounts", "suggested accounts", "推荐账号"}
    return any(
        node.attrib.get("text", "").strip().lower() in markers
        or node.attrib.get("content-desc", "").strip().lower() in markers
        for node in root.iter()
    )


def profile_surface_visible(page_source: str) -> bool:
    if private_profile_visible(page_source):
        return True
    root = ElementTree.fromstring(page_source)
    for node in root.iter():
        resource_id = node.attrib.get("resource-id", "")
        if resource_id.endswith((":id/s5x", ":id/rfc", ":id/oth", ":id/ops")):
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
        if resource_id.endswith((":id/s5y", ":id/rfd", ":id/oti", ":id/opr")):
            pending_value = text
        elif (
            resource_id.endswith((":id/s5x", ":id/rfc", ":id/oth", ":id/ops"))
            and pending_value is not None
        ):
            label = text.lower()
            if label == "follower":
                label = "followers"
            elif label == "关注":
                label = "following"
            elif label == "粉丝":
                label = "followers"
            stats[label] = parse_visible_count(pending_value)
            pending_value = None
        elif resource_id.endswith((":id/cover", ":id/dp6")):
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
