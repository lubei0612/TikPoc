from dataclasses import dataclass

from .models import ProfileMetrics

POLICY_VERSION = "posts-gte-1-v2"
SEARCH_POLICY_VERSION = "search-posts-gte-1-composite-v1"
LIVE_INTERACTION_POLICY_VERSION = "live-posts-gte-1-interaction-v1"


@dataclass(frozen=True)
class RuleDecision:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_profile(metrics: ProfileMetrics) -> RuleDecision:
    reasons: list[str] = []
    if metrics.posts < 1:
        reasons.append("insufficient_posts")
    return RuleDecision(eligible=not reasons, reasons=tuple(reasons))


def evaluate_search_profile(metrics: ProfileMetrics) -> RuleDecision:
    reasons = () if metrics.posts >= 1 else ("insufficient_posts",)
    return RuleDecision(eligible=not reasons, reasons=reasons)
