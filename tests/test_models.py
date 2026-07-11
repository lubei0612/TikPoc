import pytest

from tikpoc.models import ProfileMetrics


def test_profile_metrics_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="profile metrics must be nonnegative"):
        ProfileMetrics(following=-1, followers=2, posts=4)
