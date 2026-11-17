"""Shared assistant hint / situation-analysis quota (lifetime free + daily premium)."""

FREE_LIFETIME_LIMIT = 15
PREMIUM_DAILY_LIMIT = 10
CHARGE_FEATURES = frozenset({"analyze_entry", "assistant_hint"})


def quota_payload(premium_active, paid_premium, lifetime_used, daily_used, summary_done=False):
    if premium_active and paid_premium:
        remaining = max(0, PREMIUM_DAILY_LIMIT - daily_used)
        return {
            "premium_active": True,
            "paid_premium": True,
            "lifetime_limit": FREE_LIFETIME_LIMIT,
            "lifetime_used": lifetime_used,
            "daily_limit": PREMIUM_DAILY_LIMIT,
            "daily_used": daily_used,
            "remaining": remaining,
            "limit": PREMIUM_DAILY_LIMIT,
            "used": daily_used,
            "summary_done": summary_done,
            "exhausted": remaining <= 0,
            "mode": "daily",
        }
    remaining = max(0, FREE_LIFETIME_LIMIT - lifetime_used)
    return {
        "premium_active": bool(premium_active),
        "paid_premium": False,
        "lifetime_limit": FREE_LIFETIME_LIMIT,
        "lifetime_used": lifetime_used,
        "daily_limit": PREMIUM_DAILY_LIMIT,
        "daily_used": daily_used,
        "remaining": remaining,
        "limit": FREE_LIFETIME_LIMIT,
        "used": lifetime_used,
        "summary_done": summary_done,
        "exhausted": remaining <= 0,
        "mode": "lifetime",
    }