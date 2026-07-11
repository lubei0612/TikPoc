from tikpoc.models import ProfileMetrics
from tikpoc.rules import evaluate_profile


def test_rule_accepts_profile_above_both_boundaries() -> None:
    assert evaluate_profile(ProfileMetrics(11, 10, 4)).eligible is True


def test_rule_rejects_equal_following_and_followers() -> None:
    decision = evaluate_profile(ProfileMetrics(10, 10, 4))
    assert decision.eligible is False
    assert decision.reasons == ("following_not_greater_than_followers",)


def test_rule_rejects_exactly_three_posts() -> None:
    decision = evaluate_profile(ProfileMetrics(11, 10, 3))
    assert decision.eligible is False
    assert decision.reasons == ("post_count_not_greater_than_three",)
