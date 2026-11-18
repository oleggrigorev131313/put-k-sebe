"""Pure helpers for inactivity Telegram reminders (no YDB import)."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INACTIVITY_REMINDER_AFTER_HOURS = 20
DEFAULT_REMINDER_TIMEZONE = "Europe/Moscow"
INACTIVITY_REMINDER_TEXTS = (
    "Тебя не было почти сутки 🌿 Мысли не ждут выходных, а новые взгляды закрепляются, когда к ним возвращаешься. Загляни на 5 минут — запиши, что сегодня крутится в голове.",
    "Почти сутки без записей. Ничего страшного — но привычка смотреть на мысли иначе держится на маленьких ежедневных шагах. Одна запись сегодня — и ты продолжаешь путь.",
    "Как ты сегодня? Если что-то задело или тревожит — разбери это в дневнике, пока свежо. Пара минут, и в голове станет легче.",
    "Ты уже начал(а) менять привычные мысли — не останавливайся сейчас 💪 Результат дают ежедневные короткие записи, а не редкие длинные. Загляни сегодня.",
    "Мысли, которые не записаны, продолжают крутиться по кругу. Выгрузи их в ПЕРЕСМОТР — и посмотрим на них вместе спокойно.",
)


def safe_timezone(name):
    try:
        return ZoneInfo(str(name or "UTC")[:80])
    except ZoneInfoNotFoundError:
        return timezone.utc


def parse_iso_datetime(value):
    try:
        parsed_value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed_value.tzinfo is None:
            return parsed_value.replace(tzinfo=timezone.utc)
        return parsed_value.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return None


def effective_user_timezone(timezone_name):
    tz = str(timezone_name or "").strip()
    if not tz or tz.upper() == "UTC":
        return DEFAULT_REMINDER_TIMEZONE
    return tz[:80]


def is_reminder_quiet_hours(timezone_name, now):
    """No sends between 23:00 and 09:00 in the user's local timezone."""
    local = now.astimezone(safe_timezone(effective_user_timezone(timezone_name)))
    hour = local.hour
    return hour >= 23 or hour < 9


def inactivity_hours_elapsed(last_seen, now):
    seen = parse_iso_datetime(last_seen)
    if seen is None:
        return None
    return (now - seen).total_seconds() / 3600.0


def reminder_stale_after_last_seen(reminder_sent_at, last_seen):
    if not reminder_sent_at:
        return True
    sent = parse_iso_datetime(reminder_sent_at)
    seen = parse_iso_datetime(last_seen)
    if sent is None or seen is None:
        return True
    return sent < seen


def pick_reminder_variant(previous):
    choices = list(range(len(INACTIVITY_REMINDER_TEXTS)))
    if previous is not None and 0 <= int(previous) < len(choices):
        choices.remove(int(previous))
    return random.choice(choices)


def should_send_inactivity_reminder(
    user_id,
    last_seen,
    timezone_name,
    reminder_meta,
    now,
    *,
    ai_processing,
):
    if not str(user_id).isdigit():
        return False
    if reminder_meta.get("telegram_blocked"):
        return False
    if not ai_processing:
        return False
    if is_reminder_quiet_hours(timezone_name, now):
        return False
    hours = inactivity_hours_elapsed(last_seen, now)
    if hours is None or hours < INACTIVITY_REMINDER_AFTER_HOURS:
        return False
    return reminder_stale_after_last_seen(
        reminder_meta.get("reminder_sent_at"), last_seen
    )