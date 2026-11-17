import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from route_override import resolve_request_path  # noqa: E402


class RouteOverrideTest(unittest.TestCase):
    def test_p_query_sets_path(self):
        path, query = resolve_request_path("/", {}, {"_p": "/v1/state"})
        self.assertEqual(path, "/v1/state")
        self.assertNotIn("_p", query)

    def test_p_with_embedded_query(self):
        path, query = resolve_request_path("/", {}, {"_p": "/v1/admin/user?user_id=42"})
        self.assertEqual(path, "/v1/admin/user")
        self.assertEqual(query.get("user_id"), "42")

    def test_header_fallback(self):
        path, query = resolve_request_path("/invoke", {"x-route-path": "/v1/premium"}, {})
        self.assertEqual(path, "/v1/premium")

    def test_p_overrides_header(self):
        path, _ = resolve_request_path(
            "/",
            {"x-route-path": "/v1/premium"},
            {"_p": "/v1/trial"},
        )
        self.assertEqual(path, "/v1/trial")

    def test_gateway_path_unchanged(self):
        path, query = resolve_request_path("/v1/health", {}, {"timezone": "Europe/Moscow"})
        self.assertEqual(path, "/v1/health")
        self.assertEqual(query.get("timezone"), "Europe/Moscow")


if __name__ == "__main__":
    unittest.main()