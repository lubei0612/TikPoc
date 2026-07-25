from tikpoc.models import ProfileMetrics
from tikpoc.profile_parser import parse_profile_page, profile_surface_visible

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


def test_parse_profile_page_accepts_tiktok_46_chinese_resource_ids() -> None:
    current = """
    <hierarchy>
      <node text="@sample" resource-id="com.zhiliaoapp.musically:id/oul" />
      <node text="104" resource-id="com.zhiliaoapp.musically:id/oti" />
      <node text="关注" resource-id="com.zhiliaoapp.musically:id/oth" />
      <node text="20" resource-id="com.zhiliaoapp.musically:id/opr" />
      <node text="粉丝" resource-id="com.zhiliaoapp.musically:id/ops" />
      <node resource-id="com.zhiliaoapp.musically:id/dp6" />
      <node text="587" resource-id="com.zhiliaoapp.musically:id/vlr" />
      <node resource-id="com.zhiliaoapp.musically:id/dp6" />
      <node text="242" resource-id="com.zhiliaoapp.musically:id/vlr" />
    </hierarchy>
    """

    page = parse_profile_page(current)

    assert page.username == "sample"
    assert page.metrics == ProfileMetrics(following=104, followers=20, posts=2)
    assert page.visible_post_keys == ("587", "242")


def test_parse_profile_page_accepts_tiktok_46_spaced_chinese_count() -> None:
    current = """
    <hierarchy>
      <node text="@sample" resource-id="com.zhiliaoapp.musically:id/oul" />
      <node text="1&#160;万" resource-id="com.zhiliaoapp.musically:id/oti" />
      <node text="关注" resource-id="com.zhiliaoapp.musically:id/oth" />
      <node text="3778" resource-id="com.zhiliaoapp.musically:id/opr" />
      <node text="粉丝" resource-id="com.zhiliaoapp.musically:id/ops" />
      <node resource-id="com.zhiliaoapp.musically:id/dp6" />
    </hierarchy>
    """

    page = parse_profile_page(current)

    assert page.metrics == ProfileMetrics(following=10_000, followers=3778, posts=1)


def test_profile_surface_accepts_chinese_follow_required_marker() -> None:
    restricted = """
    <hierarchy>
      <node text="关注此账号，即可查看对方的作品和点赞的作品。" />
    </hierarchy>
    """

    assert profile_surface_visible(restricted) is True


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
