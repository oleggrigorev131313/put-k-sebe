#!/usr/bin/env python3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from journal_funnel import build_funnel_stats  # noqa: E402


class JournalFunnelTest(unittest.TestCase):
    def test_funnel_drop_between_steps(self):
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        t0 = (now - timedelta(days=1)).isoformat()
        rows = [
            {"user_id": "1", "event_kind": "step_opened", "step_number": 0, "created_at": t0},
            {"user_id": "1", "event_kind": "step_opened", "step_number": 1, "created_at": t0},
            {"user_id": "2", "event_kind": "step_opened", "step_number": 0, "created_at": t0},
            {"user_id": "1", "event_kind": "entry_completed", "step_number": -1, "created_at": t0},
        ]
        stats = build_funnel_stats(rows, days=30, now=now)
        self.assertEqual(stats["started_users"], 2)
        self.assertEqual(stats["completed_users"], 1)
        self.assertEqual(stats["steps"][0]["users"], 2)
        self.assertEqual(stats["steps"][1]["users"], 1)
        self.assertEqual(stats["steps"][1]["drop_from_previous_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()