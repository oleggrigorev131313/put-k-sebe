import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from assistant_quota import FREE_LIFETIME_LIMIT, PREMIUM_DAILY_LIMIT, quota_payload  # noqa: E402


class AssistantQuotaTest(unittest.TestCase):
    def test_free_remaining(self):
        payload = quota_payload(False, False, 5, 0)
        self.assertEqual(payload["remaining"], 10)
        self.assertEqual(payload["mode"], "lifetime")
        self.assertEqual(payload["limit"], FREE_LIFETIME_LIMIT)

    def test_free_exhausted(self):
        payload = quota_payload(False, False, 15, 0)
        self.assertTrue(payload["exhausted"])

    def test_premium_daily(self):
        payload = quota_payload(True, True, 3, 4)
        self.assertEqual(payload["remaining"], 6)
        self.assertEqual(payload["mode"], "daily")
        self.assertEqual(payload["limit"], PREMIUM_DAILY_LIMIT)


if __name__ == "__main__":
    unittest.main()