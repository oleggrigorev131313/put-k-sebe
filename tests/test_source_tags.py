#!/usr/bin/env python3
"""Unit tests for source tag parsing, slugging, and redirect routing."""

import json
import os
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from source_tags import (  # noqa: E402
    admin_source_error,
    build_tag_slug,
    enrich_tags_with_stats,
    is_imeads_start_payload,
    parse_source_tag,
    telegram_start_url,
)


class SourceTagParseTest(unittest.TestCase):
    def test_generic_tags(self):
        self.assertEqual(parse_source_tag("tiktok_summer"), "tiktok_summer")
        self.assertEqual(parse_source_tag("  vk_promo_1  "), "vk_promo_1")
        self.assertIsNone(parse_source_tag(""))
        self.assertIsNone(parse_source_tag("bad space"))
        self.assertIsNone(parse_source_tag("x" * 65))

    def test_imeads_excluded(self):
        hex32 = "a" * 32
        self.assertTrue(is_imeads_start_payload("imeads_" + hex32))
        self.assertTrue(is_imeads_start_payload("imeads__" + hex32))
        self.assertIsNone(parse_source_tag("imeads_" + hex32))
        self.assertIsNone(parse_source_tag("IMEADS__" + hex32))

    def test_bare_hex_is_generic_tag(self):
        hex32 = "a" * 32
        self.assertEqual(parse_source_tag(hex32), hex32)


class SourceTagSlugTest(unittest.TestCase):
    def test_slug_from_fields(self):
        slug = build_tag_slug("Летняя кампания", "tiktok", "june")
        self.assertRegex(slug, r"^[A-Za-z0-9_-]{1,64}$")
        self.assertTrue(slug.startswith("tiktok"))


class SourceTagStatsTest(unittest.TestCase):
    def test_enrich_tags_splits_starts_and_new_users(self):
        rows = [{"tag": "tiktok_a", "name": "A", "channel": "tiktok", "campaign": "", "created_at": ""}]
        out = enrich_tags_with_stats(
            rows,
            {"tiktok_a": 3},
            {"tiktok_a": 7},
            {"tiktok_a": {"u1", "u2"}},
            {"tiktok_a": 1},
        )
        self.assertEqual(out[0]["clicks"], 3)
        self.assertEqual(out[0]["all_starts"], 7)
        self.assertEqual(out[0]["new_users"], 2)
        self.assertEqual(out[0]["diary_users"], 1)

    def test_admin_tag_exists_ru(self):
        self.assertIn("уже существует", admin_source_error("tag_exists"))


class SourceRedirectTest(unittest.TestCase):
    def _stub_index(self):
        os.environ.setdefault("YDB_ENDPOINT", "grpcs://ydb.example:2135")
        os.environ.setdefault("YDB_DATABASE", "/local/test")
        os.environ.setdefault("BOT_TOKEN", "1:fixture")
        ydb = types.ModuleType("ydb")
        iam = types.ModuleType("ydb.iam")
        iam.MetadataUrlCredentials = lambda: None
        ydb.iam = iam
        ydb.Driver = lambda *a, **k: None
        ydb.QuerySessionPool = lambda d: None
        ydb.TypedValue = lambda v, t: (v, t)
        ydb.PrimitiveType = types.SimpleNamespace(Uint64="Uint64")
        sys.modules["ydb"] = ydb
        sys.modules["ydb.iam"] = iam
        import index as idx  # noqa: E402

        def fake_exists(tag):
            return tag == "ok_tag"

        def fake_increment(tag):
            self.clicks.append(tag)

        idx._source_tag_exists = fake_exists
        idx._source_increment_click = fake_increment
        return idx

    def setUp(self):
        self.clicks = []
        self.index = self._stub_index()

    def test_redirect_success(self):
        event = {
            "httpMethod": "GET",
            "path": "/go/ok_tag",
            "headers": {},
            "body": "",
        }
        response = self.index.handler(event, None)
        self.assertEqual(response["statusCode"], 302)
        self.assertEqual(
            response["headers"]["Location"], telegram_start_url("ok_tag")
        )
        self.assertEqual(self.clicks, ["ok_tag"])

    def test_redirect_unknown(self):
        event = {
            "httpMethod": "GET",
            "path": "/go/missing",
            "headers": {},
            "body": "",
        }
        response = self.index.handler(event, None)
        self.assertEqual(response["statusCode"], 404)
        body = json.loads(response["body"])
        self.assertEqual(body.get("error"), "unknown_tag")


if __name__ == "__main__":
    unittest.main()