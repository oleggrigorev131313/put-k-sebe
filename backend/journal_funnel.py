"""Journal wizard funnel aggregation (pure helpers)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

STEP_LABELS = (
    "1 · Ситуация",
    "2 · Эмоции",
    "3 · Автоматическая мысль",
    "4 · Факты за / против",
    "5 · Новый взгляд",
    "6 · Следующий шаг",
)


def parse_created_at(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return None


def build_funnel_stats(rows, *, days=None, now=None):
    """rows: list of dicts with user_id, event_kind, step_number, created_at."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = None
    if days is not None and int(days) > 0:
        cutoff = now - timedelta(days=int(days))

    step_users = {i: set() for i in range(6)}
    completed_users = set()

    for row in rows:
        created = parse_created_at(row.get("created_at"))
        if cutoff and (created is None or created < cutoff):
            continue
        uid = str(row.get("user_id") or "")
        if not uid:
            continue
        kind = str(row.get("event_kind") or "")
        if kind == "entry_completed":
            completed_users.add(uid)
            continue
        if kind != "step_opened":
            continue
        try:
            step = int(row.get("step_number"))
        except (TypeError, ValueError):
            continue
        if step < 0 or step > 5:
            continue
        step_users[step].add(uid)

    all_users = set().union(*step_users.values()) if step_users else set()
    started_count = len(step_users[0]) if step_users[0] else len(all_users)
    base = started_count if started_count else 1

    steps_out = []
    prev_count = None
    for i in range(6):
        count = len(step_users[i])
        pct_started = round(100.0 * count / base, 1) if started_count else 0.0
        drop = None
        if prev_count is not None and prev_count > 0:
            drop = round(100.0 * (prev_count - count) / prev_count, 1)
        steps_out.append(
            {
                "step": i,
                "label": STEP_LABELS[i],
                "users": count,
                "pct_of_started": pct_started,
                "drop_from_previous_pct": drop,
            }
        )
        prev_count = count

    completed_count = len(completed_users)
    return {
        "period_days": days if days is not None else 0,
        "started_users": started_count,
        "completed_users": completed_count,
        "completed_pct_of_started": round(100.0 * completed_count / base, 1)
        if started_count
        else 0.0,
        "steps": steps_out,
    }