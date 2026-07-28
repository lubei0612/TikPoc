from __future__ import annotations

import pytest

from tikpoc.hot_comment_planner import (
    CommentCandidate,
    CommentEvidence,
    canonical_video_id,
    choose_persona,
    rank_evidence,
    validate_candidate,
)


def test_canonical_video_id_accepts_tiktok_url_and_numeric_id() -> None:
    assert canonical_video_id(
        "https://www.tiktok.com/@zoey/video/7523456789012345678?x=1"
    ) == ("7523456789012345678")
    assert canonical_video_id("7523456789012345678") == "7523456789012345678"
    with pytest.raises(ValueError, match="video_id"):
        canonical_video_id("https://www.tiktok.com/@zoey")
    with pytest.raises(ValueError, match="video_id"):
        canonical_video_id("https://example.com/@zoey/video/7523456789012345678")


def test_rank_evidence_rewards_likes_replies_and_recency() -> None:
    now = 2_000_000
    evidence = [
        CommentEvidence("old", "Old", 500, 0, now - 30 * 86400, "en"),
        CommentEvidence("useful", "Useful", 300, 40, now - 3600, "en"),
        CommentEvidence("quiet", "Quiet", 2, 0, now - 10, "en"),
    ]

    assert [item.cid for item in rank_evidence(evidence, now_s=now)] == [
        "useful",
        "old",
        "quiet",
    ]


def test_persona_assignment_is_stable_per_video() -> None:
    personas = ("zoey", "ray", "ivy")
    assert choose_persona("7523456789012345678", personas) == choose_persona(
        "7523456789012345678", personas
    )
    assert choose_persona("7523456789012345678", personas) in personas


@pytest.mark.parametrize(
    "candidate",
    [
        CommentCandidate("", "中文", 0, "zoey"),
        CommentCandidate("这只包很好看", "中文", 0, "zoey"),
        CommentCandidate("See https://example.com", "中文", 0, "zoey"),
        CommentCandidate("Message me on WhatsApp", "中文", 0, "zoey"),
        CommentCandidate("Love this 😍✨🔥", "中文", 3, "zoey"),
    ],
)
def test_candidate_requires_bounded_english_translation_and_no_contact_hook(
    candidate: CommentCandidate,
) -> None:
    with pytest.raises(ValueError):
        validate_candidate(candidate, evidence=())


def test_candidate_rejects_exact_normalized_evidence_copy() -> None:
    evidence = (CommentEvidence("c1", "This color is everything!", 10, 1, 1, "en"),)
    candidate = CommentCandidate(
        "  This COLOR is everything! ", "这个颜色太绝了！", 0, "zoey"
    )
    with pytest.raises(ValueError, match="copy"):
        validate_candidate(candidate, evidence=evidence)


def test_valid_original_bilingual_candidate_passes() -> None:
    candidate = CommentCandidate(
        "The structure of this bag makes the whole look feel intentional ✨",
        "这只包的廓形让整套造型显得很有设计感 ✨",
        1,
        "zoey",
    )
    assert validate_candidate(candidate, evidence=()) == candidate
