import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from threading import Lock
from urllib.parse import parse_qsl

import ydb
import ydb.iam

LOG = logging.getLogger("put-k-sebe")
LOG.setLevel(logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
YDB_ENDPOINT = os.environ["YDB_ENDPOINT"]
YDB_DATABASE = os.environ["YDB_DATABASE"]
MAX_BODY_BYTES = 300_000
MAX_AUTH_AGE_SECONDS = 86_400

_driver = None
_pool = None
_db_lock = Lock()
_schema_ready = False


def _db():
    global _driver, _pool
    if _pool is not None:
        return _pool
    with _db_lock:
        if _pool is None:
            _driver = ydb.Driver(
                endpoint=YDB_ENDPOINT,
                database=YDB_DATABASE,
                credentials=ydb.iam.MetadataUrlCredentials(),
            )
            _driver.wait(fail_fast=True, timeout=10)
            _pool = ydb.QuerySessionPool(_driver)
    return _pool


def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    with _db_lock:
        if _schema_ready:
            return
        pool = _db()
        pool.execute_with_retries(
            """
            CREATE TABLE IF NOT EXISTS user_state (
                user_id Utf8 NOT NULL,
                state_json Utf8 NOT NULL,
                updated_at Timestamp,
                PRIMARY KEY (user_id)
            );
            """
        )
        pool.execute_with_retries(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                event_id Utf8 NOT NULL,
                user_id Utf8 NOT NULL,
                event_name Utf8 NOT NULL,
                created_at Timestamp,
                metadata_json Utf8,
                PRIMARY KEY (event_id)
            );
            """
        )
        _schema_ready = True


def _record_event(user_id, event_name, metadata=None):
    pool = _db()
    pool.execute_with_retries(
        """
        DECLARE $event_id AS Utf8;
        DECLARE $user_id AS Utf8;
        DECLARE $event_name AS Utf8;
        DECLARE $metadata_json AS Utf8;
        INSERT INTO analytics_events
            (event_id, user_id, event_name, created_at, metadata_json)
        VALUES
            ($event_id, $user_id, $event_name, CurrentUtcTimestamp(), $metadata_json);
        """,
        parameters={
            "$event_id": str(uuid.uuid4()),
            "$user_id": user_id,
            "$event_name": event_name,
            "$metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        },
    )


def _json_response(status, payload):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,X-Telegram-Init-Data",
        },
        "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "isBase64Encoded": False,
    }


def _request_parts(event):
    headers = event.get("headers") or {}
    headers = {str(k).lower(): str(v) for k, v in headers.items()}
    context = event.get("requestContext") or {}
    http_context = context.get("http") or {}
    method = (
        event.get("httpMethod")
        or http_context.get("method")
        or event.get("method")
        or "GET"
    ).upper()
    path = (
        event.get("path")
        or http_context.get("path")
        or event.get("rawPath")
        or "/"
    )
    body = event.get("body") or ""
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body).decode("utf-8")
    return method, path.split("?", 1)[0].rstrip("/") or "/", headers, body


def _telegram_user(init_data):
    if not BOT_TOKEN:
        raise ValueError("Telegram authentication is not configured")
    if not init_data or len(init_data) > 10_000:
        raise ValueError("Missing Telegram init data")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Duplicate Telegram init data field")
        values[key] = value

    received_hash = values.pop("hash", None)
    if not received_hash or len(received_hash) != 64:
        raise ValueError("Invalid Telegram signature")

    check_string = "\n".join(
        f"{key}={values[key]}" for key in sorted(values)
    )
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("Invalid Telegram signature")

    try:
        auth_date = int(values.get("auth_date", "0"))
    except ValueError as exc:
        raise ValueError("Invalid Telegram auth date") from exc

    now = int(time.time())
    if auth_date <= 0 or auth_date > now + 60 or now - auth_date > MAX_AUTH_AGE_SECONDS:
        raise ValueError("Telegram session expired; reopen the Mini App")

    try:
        user = json.loads(values["user"])
        user_id = str(int(user["id"]))
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Telegram user is missing") from exc

    return user_id


def _read_state(user_id):
    result = _db().execute_with_retries(
        """
        DECLARE $user_id AS Utf8;
        SELECT state_json FROM user_state WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id},
    )
    for result_set in result:
        for row in result_set.rows:
            return json.loads(row.state_json)
    return None


def _write_state(user_id, state):
    state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    _db().execute_with_retries(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $state_json AS Utf8;
        UPSERT INTO user_state (user_id, state_json, updated_at)
        VALUES ($user_id, $state_json, CurrentUtcTimestamp());
        """,
        parameters={"$user_id": user_id, "$state_json": state_json},
    )


def _validate_state(value):
    if not isinstance(value, dict):
        raise ValueError("State must be a JSON object")

    entries = value.get("entries", [])
    patterns = value.get("patterns", [])
    completed = value.get("completed", [])
    onboarding = value.get("onboarding")

    if not isinstance(entries, list) or len(entries) > 2_000:
        raise ValueError("Invalid diary entries")
    if not isinstance(patterns, list) or len(patterns) > 300:
        raise ValueError("Invalid patterns")
    if not isinstance(completed, list) or len(completed) > 5_000:
        raise ValueError("Invalid completed practices")
    if onboarding is not None and not isinstance(onboarding, dict):
        raise ValueError("Invalid onboarding data")

    clean = {
        "entries": entries,
        "patterns": patterns,
        "completed": completed,
        "onboarding": onboarding,
    }
    # Keep optional application metadata, but do not accept arbitrary top-level keys.
    for key in ("settings", "meta"):
        if key in value and isinstance(value[key], dict):
            clean[key] = value[key]

    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BODY_BYTES:
        raise ValueError("State is too large")
    return clean


def handler(event, context):
    try:
        method, path, headers, body = _request_parts(event)

        if method == "OPTIONS":
            return _json_response(204, {"ok": True})

        if path == "/health" and method == "GET":
            _ensure_schema()
            return _json_response(200, {"ok": True, "service": "put-k-sebe-api"})

        if path != "/v1/state":
            return _json_response(404, {"error": "not_found"})

        if method not in ("GET", "PUT", "DELETE"):
            return _json_response(405, {"error": "method_not_allowed"})

        user_id = _telegram_user(headers.get("x-telegram-init-data", ""))

        if method == "GET":
            _ensure_schema()
            state = _read_state(user_id)
            _record_event(user_id, "app_opened")
            return _json_response(
                200,
                {"found": state is not None, "state": state},
            )

        if method == "PUT":
            if len(body.encode("utf-8")) > MAX_BODY_BYTES:
                return _json_response(413, {"error": "payload_too_large"})
            try:
                payload = json.loads(body or "{}")
                state = _validate_state(payload.get("state"))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                return _json_response(400, {"error": str(exc)})

            _ensure_schema()
            previous = _read_state(user_id)
            _write_state(user_id, state)

            if previous is None:
                _record_event(user_id, "user_registered")
            if previous is None or not previous.get("onboarding"):
                if state.get("onboarding") and not state["onboarding"].get("skipped"):
                    _record_event(user_id, "onboarding_completed")

            return _json_response(200, {"ok": True, "saved": True})

        if method == "DELETE":
            _ensure_schema()
            _db().execute_with_retries(
                """
                DECLARE $user_id AS Utf8;
                DELETE FROM user_state WHERE user_id = $user_id;
                """,
                parameters={"$user_id": user_id},
            )
            _record_event(user_id, "user_data_deleted")
            return _json_response(200, {"ok": True, "deleted": True})

        return _json_response(405, {"error": "method_not_allowed"})

    except ValueError as exc:
        return _json_response(401, {"error": str(exc)})
    except Exception as exc:
        # Do not log request bodies, Telegram initData, tokens, or diary content.
        LOG.error("API request failed: %s", type(exc).__name__)
        return _json_response(503, {"error": "service_unavailable"})
