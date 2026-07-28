from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class CommentEvidence:
    cid: str
    text: str
    likes: int
    replies: int
    created_at: int
    language: str


@dataclass(frozen=True)
class CommentCandidate:
    english: str
    chinese: str
    emoji_count: int
    persona_id: str


def canonical_video_id(value: str) -> str:
    normalized = str(value).strip()
    if normalized.isdigit() and 10 <= len(normalized) <= 30:
        return normalized
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").casefold()
    if hostname != "tiktok.com" and not hostname.endswith(".tiktok.com"):
        raise ValueError("video_id is required")
    match = re.search(r"/video/(\d{10,30})(?:/|$)", parsed.path)
    if match is None:
        raise ValueError("video_id is required")
    return match.group(1)


def rank_evidence(
    evidence: list[CommentEvidence] | tuple[CommentEvidence, ...], *, now_s: int
) -> list[CommentEvidence]:
    def score(item: CommentEvidence) -> tuple[float, int, str]:
        age_days = max(0, now_s - item.created_at) / 86_400
        recency = 100 / (1 + age_days)
        return (item.likes + item.replies * 6 + recency, item.created_at, item.cid)

    return sorted(evidence, key=score, reverse=True)


def choose_persona(video_id: str, persona_ids: tuple[str, ...]) -> str:
    if not persona_ids:
        raise ValueError("persona_ids are required")
    digest = hashlib.sha256(canonical_video_id(video_id).encode()).digest()
    return persona_ids[int.from_bytes(digest[:8], "big") % len(persona_ids)]


def validate_candidate(
    candidate: CommentCandidate,
    *,
    evidence: list[CommentEvidence] | tuple[CommentEvidence, ...],
) -> CommentCandidate:
    english = candidate.english.strip()
    chinese = candidate.chinese.strip()
    if not 1 <= len(english) <= 220:
        raise ValueError("English comment must contain 1..220 characters")
    if not chinese:
        raise ValueError("Chinese translation is required")
    if not re.search(r"[A-Za-z]", english) or re.search(r"[\u3400-\u9fff]", english):
        raise ValueError("publish text must be English")
    lowered = english.casefold()
    if re.search(r"(?:https?://|www\.|\b(?:whatsapp|telegram|wechat)\b)", lowered):
        raise ValueError("URL or contact destination is not allowed")
    actual_emoji_count = sum(_is_emoji(character) for character in english)
    if actual_emoji_count > 2 or candidate.emoji_count != actual_emoji_count:
        raise ValueError("at most two emoji code points are allowed")
    normalized = _normalize_copy(english)
    if any(normalized == _normalize_copy(item.text) for item in evidence):
        raise ValueError("exact evidence copy is not allowed")
    if not candidate.persona_id.strip():
        raise ValueError("persona_id is required")
    return candidate


def _normalize_copy(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _is_emoji(character: str) -> bool:
    codepoint = ord(character)
    return 0x1F000 <= codepoint <= 0x1FAFF or 0x2600 <= codepoint <= 0x27BF
