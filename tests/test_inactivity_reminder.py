#!/usr/bin/env python3
"""Unit tests for inactivity reminder selection helpers."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from inactivity_reminder import (  # noqa: E402
    INACTIVITY_REMINDER_AFTER_HOURS,
    INACTIVITY_REMINDER_TEXTS,
    effective_user_timezone,
    inactivity_hours_elapsed,
    pick_reminder_variant,
    reminder_stale_after_last_seen,
    should_send_inactivity_reminder,
)


class InactivityReminderLogicTest(unittest.TestCase):
    def test_default_timezone_when_utc(self):
        self.assertEqual(effective_user_timezone("UTC"), "Europe/Moscow")
        self.assertEqual(effective_user_timezone(""), "Europe/Moscow")

    def test_inactivity_threshold(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=INACTIVITY_REMINDER_AFTER_HOURS + 1)).isoformat()
        self.assertGreaterEqual(inactivity_hours_elapsed(old, now), INACTIVITY_REMINDER_AFTER_HOURS)
        recent = (now - timedelta(hours=1)).isoformat()
        self.assertLess(inactivity_hours_elapsed(recent, now), INACTIVITY_REMINDER_AFTER_HOURS)

    def test_reminder_not_repeated_until_new_visit(self):
        last_seen = "2026-01-10T12:00:00+00:00"
        sent_after = "2026-01-11T12:00:00+00:00"
        self.assertFalse(reminder_stale_after_last_seen(sent_after, last_seen))
        sent_before = "2026-01-09T12:00:00+00:00"
        self.assertTrue(reminder_stale_after_last_seen(sent_before, last_seen))

    def test_pick_variant_never_repeats_previous(self):
        for previous in range(len(INACTIVITY_REMINDER_TEXTS)):
            for _ in range(30):
                variant = pick_reminder_variant(previous)
                self.assertNotEqual(variant, previous)
                self.assertIn(variant, range(len(INACTIVITY_REMINDER_TEXTS)))

    def test_should_send_filters(self):
        now = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        last_seen = (now - timedelta(hours=25)).isoformat()
        meta = {"telegram_blocked": False, "reminder_sent_at": ""}
        self.assertTrue(
            should_send_inactivity_reminder(
                "42", last_seen, meta, now, ai_processing=True
            )
        )
        self.assertFalse(
            should_send_inactivity_reminder(
                "42",
                last_seen,
                {**meta, "telegram_blocked": True},
                now,
                ai_processing=True,
            )
        )
        night = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
        night_last_seen = (night - timedelta(hours=25)).isoformat()
        self.assertTrue(
            should_send_inactivity_reminder(
                "42", night_last_seen, meta, night, ai_processing=True
            )
        )


if __name__ == "__main__":
    unittest.main()