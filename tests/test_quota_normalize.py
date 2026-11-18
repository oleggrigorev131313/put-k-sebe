"""Mirror frontend normalizeQuotaPayload remaining logic (index.html)."""

import unittest


def quota_remaining(payload):
    data = dict(payload or {})
    premium = bool(data.get("paid_premium") or data.get("mode") == "daily")
    if data.get("remaining") is not None:
        return max(0, int(data["remaining"]))
    if premium:
        limit = int(data.get("daily_limit") or data.get("limit") or 10)
        used = int(data.get("daily_used") if data.get("daily_used") is not None else data.get("used") or 0)
    else:
        limit = int(data.get("lifetime_limit") or data.get("limit") or 15)
        used = int(
            data.get("lifetime_used") if data.get("lifetime_used") is not None else data.get("used") or 0
        )
    return max(0, limit - used)


class QuotaNormalizeTest(unittest.TestCase):
    def test_explicit_remaining_zero(self):
        self.assertEqual(quota_remaining({"remaining": 0, "mode": "lifetime"}), 0)

    def test_lifetime_used_exhausted_without_remaining_field(self):
        self.assertEqual(
            quota_remaining({"lifetime_limit": 15, "lifetime_used": 15, "used": 0, "mode": "lifetime"}),
            0,
        )

    def test_daily_premium_remaining(self):
        self.assertEqual(
            quota_remaining({"mode": "daily", "paid_premium": True, "daily_limit": 10, "daily_used": 7}),
            3,
        )


if __name__ == "__main__":
    unittest.main()