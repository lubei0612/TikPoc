from tikpoc.models import ProfileMetrics
from tikpoc.profile_parser import (
    parse_profile_page,
    video_controls_visible,
)


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
  <node text="101" resource-id="com.zhiliaoapp.musically:id/tv_play_count" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
  <node text="202" resource-id="com.zhiliaoapp.musically:id/tv_play_count" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
  <node text="303" resource-id="com.zhiliaoapp.musically:id/tv_play_count" />
  <node resource-id="com.zhiliaoapp.musically:id/cover" />
  <node text="404" resource-id="com.zhiliaoapp.musically:id/tv_play_count" />
</hierarchy>
"""


def test_parse_profile_page_reads_stats_and_visible_posts() -> None:
    page = parse_profile_page(PROFILE_XML)

    assert page.username == "sample"
    assert page.metrics == ProfileMetrics(following=12, followers=10, posts=4)
    assert page.visible_post_count == 4
    assert page.visible_post_keys == ("101", "202", "303", "404")


def test_parse_profile_page_accepts_appium_element_tags() -> None:
    appium_source = PROFILE_XML.replace("<node ", "<android.widget.TextView ")

    page = parse_profile_page(appium_source)

    assert page.username == "sample"
    assert page.metrics == ProfileMetrics(following=12, followers=10, posts=4)


def test_video_controls_visible_reads_semantic_share_description() -> None:
    source = (
        '<hierarchy><node content-desc="Share video. 42 shares" '
        'bounds="[900,1000][1000,1100]" /></hierarchy>'
    )

    assert video_controls_visible(source) is True
    assert video_controls_visible("<hierarchy />") is False


def test_parse_profile_page_accepts_current_tiktok_resource_ids() -> None:
    current = """
    <hierarchy>
      <node text="@sample" resource-id="com.zhiliaoapp.musically:id/rgn" />
      <node text="326" resource-id="com.zhiliaoapp.musically:id/rfd" />
      <node text="Following" resource-id="com.zhiliaoapp.musically:id/rfc" />
      <node text="198" resource-id="com.zhiliaoapp.musically:id/rfd" />
      <node text="Followers" resource-id="com.zhiliaoapp.musically:id/rfc" />
      <node resource-id="com.zhiliaoapp.musically:id/cover" />
      <node text="57" resource-id="com.zhiliaoapp.musically:id/z9h" />
      <node resource-id="com.zhiliaoapp.musically:id/cover" />
      <node text="30" resource-id="com.zhiliaoapp.musically:id/z9h" />
    </hierarchy>
    """

    page = parse_profile_page(current)

    assert page.username == "sample"
    assert page.metrics == ProfileMetrics(following=326, followers=198, posts=2)
    assert page.visible_post_keys == ("57", "30")


def test_parse_profile_page_accepts_singular_follower_label() -> None:
    singular = PROFILE_XML.replace('text="Followers"', 'text="Follower"')

    page = parse_profile_page(singular)

    assert page.metrics.followers == 10


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
