"""Route resolution for direct Yandex Cloud Function HTTP invoke (gateway bypass)."""

from urllib.parse import parse_qsl


def _normalize_path(path_part):
    path = (path_part or "/").split("?", 1)[0].rstrip("/") or "/"
    return path


def _merge_route_query(route_target, query):
    if not route_target:
        return "/", query
    route_target = str(route_target).strip()
    if "?" in route_target:
        path_part, qs = route_target.split("?", 1)
        path = _normalize_path(path_part)
        for key, value in parse_qsl(qs, keep_blank_values=True):
            if key == "_p":
                continue
            query.setdefault(key, value)
        return path, query
    return _normalize_path(route_target), query


def resolve_request_path(path, headers, query):
    """
    Apply _p query param or X-Route-Path when the function is called without API Gateway.
    Mutates query (removes _p). Returns (path, query).
    """
    if query is None:
        query = {}
    headers = headers or {}
    override = ""
    if query.get("_p") is not None:
        override = str(query.pop("_p"))
    elif headers.get("x-route-path"):
        override = str(headers.get("x-route-path")).strip()
    if override:
        if not override.startswith("/"):
            override = "/" + override.lstrip("/")
        return _merge_route_query(override, query)
    return _normalize_path(path or "/"), query