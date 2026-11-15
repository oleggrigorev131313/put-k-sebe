#!/usr/bin/env python3
"""Offline tests for iMe Ads click_id parsing (no YDB import)."""
import unittest


def parse_imeads_click_id(raw):
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    lowered = value.casefold()
    for prefix in ("imeads__", "imeads_"):
        if not lowered.startswith(prefix):
            continue
        click_id = value[len(prefix) :].strip()
        if not click_id or len(click_id) > 128 or " " in click_id or "\t" in click_id:
            continue
        if prefix == "imeads_" and lowered.startswith("imeads__"):
            continue
        return click_id
    return None


class ImeadsParseTest(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(parse_imeads_click_id("imeads__abc123"), "abc123")
        self.assertEqual(parse_imeads_click_id("imeads_abc123"), "abc123")
        self.assertEqual(parse_imeads_click_id("IMEADS__X"), "X")
        self.assertIsNone(parse_imeads_click_id("other"))
        self.assertIsNone(parse_imeads_click_id("imeads__"))


if __name__ == "__main__":
    unittest.main()