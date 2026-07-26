from tikpoc.models import ProfileMetrics
from tikpoc.rules import evaluate_profile, evaluate_search_profile


def test_rule_accepts_profile_with_one_post() -> None:
    assert evaluate_profile(ProfileMetrics(11, 10, 1)).eligible is True


def test_rule_rejects_equal_following_and_followers() -> None:
    decision = evaluate_profile(ProfileMetrics(10, 10, 4))
    assert decision.eligible is False
    assert decision.reasons == ("following_not_greater_than_followers",)


def test_rule_rejects_zero_posts() -> None:
    decision = evaluate_profile(ProfileMetrics(11, 10, 0))
    assert decision.eligible is False
    assert decision.reasons == ("insufficient_posts",)


def test_search_rule_ignores_follow_ratio_and_requires_one_post() -> None:
    assert evaluate_search_profile(ProfileMetrics(1, 999, 1)).eligible is True
    decision = evaluate_search_profile(ProfileMetrics(999, 1, 0))
    assert decision.eligible is False
    assert decision.reasons == ("insufficient_posts",)
