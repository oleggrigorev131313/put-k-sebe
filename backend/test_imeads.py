#!/usr/bin/env python3
import os
import unittest

os.environ.setdefault("YDB_ENDPOINT", "grpcs://test")
os.environ.setdefault("YDB_DATABASE", "/test")

import index


class ImeadsParseTest(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(index._parse_imeads_click_id("imeads__abc123"), "abc123")
        self.assertEqual(index._parse_imeads_click_id("imeads_abc123"), "abc123")
        self.assertEqual(index._parse_imeads_click_id("IMEADS__X"), "X")
        self.assertIsNone(index._parse_imeads_click_id("other"))
        self.assertIsNone(index._parse_imeads_click_id("imeads__"))


if __name__ == "__main__":
    unittest.main()