"""UTM-style source tags: parsing, slugging, and admin stats helpers."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

SOURCE_TAG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SOURCE_CHANNELS = frozenset(
    {"tiktok", "instagram", "youtube", "vk", "threads", "direct", "other"}
)
TELEGRAM_BOT_USERNAME = "kptprorabotkabot"


def is_imeads_start_payload(raw):
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    lowered = text.casefold()
    return lowered.startswith("imeads_") or lowered.startswith("imeads__")


def parse_source_tag(raw):
    """Return a generic source tag or None (iMe and invalid payloads excluded)."""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value or is_imeads_start_payload(value):
        return None
    if not SOURCE_TAG_RE.fullmatch(value):
        return None
    return value


def build_tag_slug(name, channel, campaign):
    channel = str(channel or "").strip().lower()
    raw = " ".join(x for x in (channel, campaign, name) if x and str(x).strip())
    ascii_part = raw.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9_-]+", "_", ascii_part.lower()).strip("_")[:64]
    if not slug or not SOURCE_TAG_RE.fullmatch(slug):
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        slug = f"{channel}_{digest}" if channel else f"src_{digest}"
        slug = slug[:64]
    if not SOURCE_TAG_RE.fullmatch(slug):
        slug = f"src_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
    return slug[:64]


def telegram_start_url(tag):
    return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={tag}"


ADMIN_SOURCE_ERRORS_RU = {
    "tag_exists": "Метка с таким slug уже существует — задайте другое название или укажите slug вручную.",
    "invalid_name": "Укажите название метки.",
    "invalid_channel": "Выберите канал из списка.",
    "invalid_tag": "Slug метки: латиница, цифры, _ и -, до 64 символов.",
    "unknown_tag": "Метка не найдена.",
}


def admin_source_error(code):
    key = str(code or "").strip()
    return ADMIN_SOURCE_ERRORS_RU.get(key, key or "Ошибка")


def enrich_tags_with_stats(
    tag_rows, clicks_by_tag, all_starts_by_tag, users_by_tag, diary_users_by_tag
):
    """tag_rows: list of dicts with tag, name, channel, campaign, created_at."""
    out = []
    for row in tag_rows:
        tag = str(row.get("tag") or "")
        out.append(
            {
                **row,
                "clicks": int(clicks_by_tag.get(tag, 0)),
                "all_starts": int(all_starts_by_tag.get(tag, 0)),
                "new_users": len(users_by_tag.get(tag, set())),
                "diary_users": int(diary_users_by_tag.get(tag, 0)),
            }
        )
    return out


def group_users_by_tag(user_source_rows):
    grouped = defaultdict(set)
    for row in user_source_rows:
        tag = str(row.get("tag") or "")
        uid = str(row.get("user_id") or "")
        if tag and uid:
            grouped[tag].add(uid)
    return grouped


def diary_users_per_tag(users_by_tag, user_has_diary):
    """
    users_by_tag: tag -> set(user_id)
    user_has_diary: callable(user_id) -> bool
    """
    out = {}
    for tag, uids in users_by_tag.items():
        out[tag] = sum(1 for uid in uids if user_has_diary(uid))
    return out