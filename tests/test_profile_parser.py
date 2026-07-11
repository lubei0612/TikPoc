from tikpoc.models import ProfileMetrics
from tikpoc.profile_parser import parse_profile_page


PROFILE_XML = """
<hierarchy>
  <node text="@sample" resource-id="com.zhiliaoapp.musically:id/s7e" />
  <node text="12" resource-id="com.zhiliaoapp.musically:id/s5y" />
  <node text="Following" resource-id="com.zhiliaoapp.musically:id/s5x" />
  <node text="10" resource-id="com.zhiliaoapp.musically:id/s5y" />
  <node text="Followers" resource-id="com.zhiliaoapp.musically:id/s5x" />
  <node text="93" resource-id="com.zhiliaoapp.musically:id/s5y" />
  <node text="Likes" resource-id="com.zhiliaoapp.musically:id/s5x" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
</hierarchy>
"""


def test_parse_profile_page_reads_stats_and_visible_posts() -> None:
    page = parse_profile_page(PROFILE_XML)

    assert page.username == "sample"
    assert page.metrics == ProfileMetrics(following=12, followers=10, posts=4)
    assert page.visible_post_count == 4


def test_parse_profile_page_rejects_missing_required_stat() -> None:
    incomplete = PROFILE_XML.replace(
        '<node text="10" resource-id="com.zhiliaoapp.musically:id/s5y" />\n'
        '  <node text="Followers" resource-id="com.zhiliaoapp.musically:id/s5x" />',
        "",
    )

    try:
        parse_profile_page(incomplete)
    except ValueError as error:
        assert str(error) == "profile metrics are incomplete"
    else:
        raise AssertionError("incomplete profile was accepted")
