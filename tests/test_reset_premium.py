import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from reset_premium import (  # noqa: E402
    EXPIRED_TRIAL_AT,
    _active_trial_users,
    paid_users_from_purchases,
)


class ResetPremiumSafetyTest(unittest.TestCase):
    def test_paid_users_only_active(self):
        rows = [
            {"user_id": "1", "status": "active"},
            {"user_id": "2", "status": "refunded"},
            {"user_id": "1", "status": "active"},
        ]
        self.assertEqual(paid_users_from_purchases(rows), {"1"})

    def test_active_trial_detection(self):
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        future = (now + timedelta(days=3)).isoformat()
        past = (now - timedelta(days=1)).isoformat()
        trials = [
            {"user_id": "a", "expires_at": future},
            {"user_id": "b", "expires_at": past},
        ]
        self.assertEqual(_active_trial_users(trials, now), {"a"})

    def test_expired_constant_is_safe_iso(self):
        self.assertTrue(EXPIRED_TRIAL_AT.startswith("1970"))


if __name__ == "__main__":
    unittest.main()