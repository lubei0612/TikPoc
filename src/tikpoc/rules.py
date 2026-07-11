from dataclasses import dataclass

from .models import ProfileMetrics


@dataclass(frozen=True)
class RuleDecision:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_profile(metrics: ProfileMetrics) -> RuleDecision:
    reasons: list[str] = []
    if metrics.following <= metrics.followers:
        reasons.append("following_not_greater_than_followers")
    if metrics.posts <= 3:
        reasons.append("post_count_not_greater_than_three")
    return RuleDecision(eligible=not reasons, reasons=tuple(reasons))
