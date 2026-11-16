#!/usr/bin/env python3
"""Offline tests for iMe Ads click_id parsing (no YDB import)."""
import re
import unittest

IMEADS_CLICK_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SAMPLE = "a" * 31 + "b"


def parse_imeads_click_id(raw):
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    lowered = value.casefold()
    if lowered.startswith("imeads__"):
        value = value[8:].strip()
    elif lowered.startswith("imeads_"):
        value = value[7:].strip()
    value = value.casefold()
    if IMEADS_CLICK_ID_RE.match(value):
        return value
    return None


class ImeadsParseTest(unittest.TestCase):
    def test_prefixes_and_bare_hex(self):
        self.assertEqual(parse_imeads_click_id("imeads__" + SAMPLE), SAMPLE)
        self.assertEqual(parse_imeads_click_id("imeads_" + SAMPLE), SAMPLE)
        self.assertEqual(parse_imeads_click_id(SAMPLE), SAMPLE)
        self.assertIsNone(parse_imeads_click_id("imeads__tooshort"))
        self.assertIsNone(parse_imeads_click_id("other"))


if __name__ == "__main__":
    unittest.main()