from dataclasses import dataclass

from .models import ProfileMetrics

POLICY_VERSION = "following-gt-followers-posts-gte-1-v1"


@dataclass(frozen=True)
class RuleDecision:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_profile(metrics: ProfileMetrics) -> RuleDecision:
    reasons: list[str] = []
    if metrics.following <= metrics.followers:
        reasons.append("following_not_greater_than_followers")
    if metrics.posts < 1:
        reasons.append("insufficient_posts")
    return RuleDecision(eligible=not reasons, reasons=tuple(reasons))
