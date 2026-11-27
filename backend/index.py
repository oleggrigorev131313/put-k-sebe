import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from threading import RLock
from urllib.parse import parse_qsl, unquote

import ydb
from assistant_quota import (
    CHARGE_FEATURES,
    FREE_LIFETIME_LIMIT,
    PREMIUM_DAILY_LIMIT,
    quota_payload,
)
from inactivity_reminder import (
    INACTIVITY_REMINDER_TEXTS,
    pick_reminder_variant,
    should_send_inactivity_reminder,
)
from journal_funnel import build_funnel_stats
from route_override import resolve_request_path
from source_tags import (
    SOURCE_CHANNELS,
    SOURCE_TAG_RE,
    admin_source_error,
    build_tag_slug,
    diary_users_per_tag,
    enrich_tags_with_stats,
    group_users_by_tag,
    parse_source_tag,
    telegram_start_url,
)
import ydb.iam

LOG = logging.getLogger("put-k-sebe")
LOG.setLevel(logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ANALYTICS_SECRET = os.environ.get("ANALYTICS_SECRET", BOT_TOKEN)
YDB_ENDPOINT = os.environ["YDB_ENDPOINT"]
YDB_DATABASE = os.environ["YDB_DATABASE"]
MAX_BODY_BYTES = 300_000
MAX_AUTH_AGE_SECONDS = 86_400
TRIBUTE_API_KEY = os.environ.get("TRIBUTE_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-flash"
TRIBUTE_PRODUCTS = {"177388": 30, "177544": 90, "177545": 365}
TRIBUTE_PRODUCT_AMOUNT_RUB = {"177388": 490, "177544": 1323, "177545": 4704}
IMEADS_SECRET_KEY = os.environ.get("IMEADS_SECRET_KEY", "").strip()
IMEADS_POSTBACK_URL = "https://ad.api.imem.me/postback"
IMEADS_CLICK_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_driver = None
_pool = None
_db_lock = RLock()
TABLES = (
    "user_consents",
    "user_state",
    "user_profiles",
    "analytics_events",
    "premium_purchases",
    "premium_trials",
    "ai_analyses",
    "daily_summary_claims",
    "ai_usage_events",
    "imeads_attribution",
    "assistant_quota",
    "user_reminders",
    "journal_funnel_events",
    "source_tags",
    "user_sources",
    "source_clicks",
    "source_starts",
)

# Night push at 23:59 is disabled; the timer may still invoke the function.
DAILY_SUMMARY_TELEGRAM_PUSH_ENABLED = os.environ.get(
    "DAILY_SUMMARY_TELEGRAM_PUSH_ENABLED", ""
).strip().lower() in ("1", "true", "yes")

_MISSING_TABLE_MARKERS = (
    "does not exist",
    "doesn't exist",
    "doesnt exist",
    "path not found",
    "no such table",
    "cannot find table",
    "unknown table",
    "table not found",
    "epathstatedoesnotexist",
)


class TableMissing(RuntimeError):
    def __init__(self, table_name):
        self.table_name = table_name
        super().__init__("table_missing:" + table_name)


def _error_text(exc):
    return " ".join(str(exc).split())


def _is_missing_table_error(exc):
    raw = _error_text(exc).lower()
    if "400080" in raw or "schema operations" in raw or "eschemeopcreatetable" in raw:
        return False
    return any(marker in raw for marker in _MISSING_TABLE_MARKERS)


def _table_from_text(*parts):
    blob = " ".join(part for part in parts if part).lower()
    found = [name for name in TABLES if name in blob]
    if len(found) == 1:
        return found[0]
    return None


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


def _execute(query, parameters=None, table=None):
    try:
        if parameters is None:
            return _db().execute_with_retries(query)
        return _db().execute_with_retries(query, parameters=parameters)
    except TableMissing:
        raise
    except Exception as exc:
        if not _is_missing_table_error(exc):
            raise
        name = table or _table_from_text(_error_text(exc), query)
        if not name:
            name = "unknown"
        LOG.error("YDB table missing table=%s detail=%s", name, _error_text(exc)[:400])
        raise TableMissing(name) from exc


def _probe_table(name):
    if name not in TABLES:
        raise ValueError("unexpected table " + name)
    _execute("SELECT * FROM %s LIMIT 1;" % name, table=name)


CONSENT_VERSION = "2026-11-14-v1"

def _get_user_consents(user_id):
    result = _execute("""
        DECLARE $user_id AS Utf8;
        SELECT ai_processing, ai_history_storage, consent_version, updated_at
        FROM user_consents WHERE user_id = $user_id;
        """, parameters={"$user_id": user_id})
    for result_set in result:
        for row in result_set.rows:
            return {"ai_processing": bool(row.ai_processing),
                    "ai_history_storage": bool(row.ai_history_storage),
                    "version": str(row.consent_version), "updated_at": str(row.updated_at)}
    return {"ai_processing": False, "ai_history_storage": False, "version": CONSENT_VERSION, "updated_at": None}

def _save_user_consents(user_id, processing, history):
    from datetime import datetime, timezone
    updated = datetime.now(timezone.utc).isoformat()
    _execute("""
        DECLARE $user_id AS Utf8;
        DECLARE $processing AS Bool;
        DECLARE $history AS Bool;
        DECLARE $version AS Utf8;
        DECLARE $updated AS Utf8;
        UPSERT INTO user_consents (user_id, ai_processing, ai_history_storage, consent_version, updated_at)
        VALUES ($user_id, $processing, $history, $version, $updated);
        """, parameters={"$user_id": user_id, "$processing": processing, "$history": history, "$version": CONSENT_VERSION, "$updated": updated})
    return _get_user_consents(user_id)

def _require_ai_processing(user_id):
    if not _get_user_consents(user_id)["ai_processing"]:
        return _json_response(403, {"error": "ai_consent_required", "message": "AI-обработка не разрешена."})
    return None

def _delete_ai_history(user_id):
    _execute("""
        DECLARE $user_id AS Utf8;
        DELETE FROM ai_analyses WHERE user_id = $user_id;
        """, parameters={"$user_id": user_id})
    state = _read_state(user_id)
    if isinstance(state, dict):
        for entry in state.get("entries") or []:
            if isinstance(entry, dict):
                entry.pop("analysis", None)
        state["chat"] = []
        _write_state(user_id, state)

def _parse_imeads_click_id(raw):
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


def _imeads_read(user_id):
    result = _execute(
        """
        DECLARE $user_id AS Utf8;
        SELECT click_id, sent_json FROM imeads_attribution WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id},
        table="imeads_attribution",
    )
    for result_set in result:
        for row in result_set.rows:
            try:
                flags = json.loads(str(row.sent_json or "{}"))
                if not isinstance(flags, dict):
                    flags = {}
            except (json.JSONDecodeError, TypeError, ValueError):
                flags = {}
            click_id = _parse_imeads_click_id(str(row.click_id))
            return {"click_id": click_id, "flags": flags}
    return None


def _imeads_persist_flags(user_id, click_id, captured_at, flags):
    _execute(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $click_id AS Utf8;
        DECLARE $captured AS Utf8;
        DECLARE $sent AS Utf8;
        UPSERT INTO imeads_attribution (user_id, click_id, captured_at, sent_json)
        VALUES ($user_id, $click_id, $captured, $sent);
        """,
        parameters={
            "$user_id": user_id,
            "$click_id": click_id,
            "$captured": captured_at,
            "$sent": json.dumps(flags, ensure_ascii=False, separators=(",", ":")),
        },
        table="imeads_attribution",
    )


def _imeads_redact_body(text):
    if not text:
        return ""
    return re.sub(r"(?i)(key=)[^&\s\"']+", r"\1REDACTED", str(text))[:500]


def _imeads_fire_postback(click_id, event, value=None, currency=None, log_errors=True):
    click_id = _parse_imeads_click_id(click_id)
    if not IMEADS_SECRET_KEY or not click_id:
        return {"ok": False, "status": None, "body": "", "skipped": True}
    import urllib.error
    import urllib.parse
    import urllib.request

    params = {"key": IMEADS_SECRET_KEY, "click_id": click_id, "event": event}
    if value is not None:
        params["value"] = str(int(value))
    if currency:
        params["currency"] = str(currency)[:8]
    url = IMEADS_POSTBACK_URL + "?" + urllib.parse.urlencode(params)
    status = None
    body = ""
    try:
        request = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(request, timeout=3) as response:
            status = response.status
            body = response.read(512).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            body = exc.read(512).decode("utf-8", errors="replace")
        except Exception:
            body = ""
    except Exception:
        if log_errors:
            LOG.info("imeads postback event=%s failed transport", event)
        return {"ok": False, "status": status, "body": _imeads_redact_body(body)}
    ok = status is not None and 200 <= status < 300
    if log_errors and not ok:
        LOG.info(
            "imeads postback event=%s status=%s body=%s",
            event,
            status,
            _imeads_redact_body(body)[:200],
        )
    return {"ok": ok, "status": status, "body": _imeads_redact_body(body)}


def _imeads_send_once(user_id, event, value=None, currency=None):
    if not IMEADS_SECRET_KEY:
        return
    try:
        row = _imeads_read(user_id)
    except TableMissing:
        return
    if not row or not row.get("click_id"):
        return
    flag_key = event if value is None else f"{event}:{int(value)}:{currency or ''}"
    flags = dict(row.get("flags") or {})
    if flags.get(flag_key):
        return
    if not row.get("click_id"):
        return
    sent = _imeads_fire_postback(row["click_id"], event, value, currency)
    if not sent.get("ok"):
        return
    flags[flag_key] = True
    try:
        attrib = _execute(
            """
            DECLARE $user_id AS Utf8;
            SELECT click_id, captured_at FROM imeads_attribution WHERE user_id = $user_id;
            """,
            parameters={"$user_id": user_id},
            table="imeads_attribution",
        )
        captured = None
        click_id = row["click_id"]
        for rs in attrib:
            for att in rs.rows:
                captured = str(att.captured_at)
                click_id = str(att.click_id)
        if not captured:
            from datetime import datetime, timezone

            captured = datetime.now(timezone.utc).isoformat()
        _imeads_persist_flags(user_id, click_id, captured, flags)
    except TableMissing:
        return
    except Exception:
        pass


def _imeads_capture_click_id(user_id, raw):
    click_id = _parse_imeads_click_id(raw)
    if not click_id:
        return None
    try:
        existing = _imeads_read(user_id)
    except TableMissing:
        return None
    if existing:
        return existing["click_id"]
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    flags = {"new_user": True}
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8;
            DECLARE $click_id AS Utf8;
            DECLARE $captured AS Utf8;
            DECLARE $sent AS Utf8;
            INSERT INTO imeads_attribution (user_id, click_id, captured_at, sent_json)
            VALUES ($user_id, $click_id, $captured, $sent);
            """,
            parameters={
                "$user_id": user_id,
                "$click_id": click_id,
                "$captured": now,
                "$sent": json.dumps(flags, ensure_ascii=False, separators=(",", ":")),
            },
            table="imeads_attribution",
        )
    except Exception:
        existing = _imeads_read(user_id)
        if existing:
            return existing["click_id"]
        return None
    _imeads_fire_postback(click_id, "new_user")
    return click_id


def _source_capture_first_touch(user_id, raw):
    tag = parse_source_tag(raw)
    if not tag:
        return None
    try:
        existing = _execute(
            """
            DECLARE $user_id AS Utf8;
            SELECT tag FROM user_sources WHERE user_id = $user_id;
            """,
            parameters={"$user_id": str(user_id)},
            table="user_sources",
        )
        for rs in existing:
            for row in rs.rows:
                return str(row.tag)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        _execute(
            """
            DECLARE $user_id AS Utf8;
            DECLARE $tag AS Utf8;
            DECLARE $seen AS Utf8;
            INSERT INTO user_sources (user_id, tag, first_seen_at)
            VALUES ($user_id, $tag, $seen);
            """,
            parameters={
                "$user_id": str(user_id),
                "$tag": tag,
                "$seen": now,
            },
            table="user_sources",
        )
        return tag
    except TableMissing:
        return None
    except Exception:
        try:
            existing = _execute(
                """
                DECLARE $user_id AS Utf8;
                SELECT tag FROM user_sources WHERE user_id = $user_id;
                """,
                parameters={"$user_id": str(user_id)},
                table="user_sources",
            )
            for rs in existing:
                for row in rs.rows:
                    return str(row.tag)
        except Exception:
            pass
        return None


def _source_tag_exists(tag):
    try:
        result = _execute(
            """
            DECLARE $tag AS Utf8;
            SELECT tag FROM source_tags WHERE tag = $tag;
            """,
            parameters={"$tag": tag},
            table="source_tags",
        )
        for rs in result:
            for _row in rs.rows:
                return True
    except TableMissing:
        return False
    return False


def _source_increment_click(tag):
    count = 0
    try:
        result = _execute(
            """
            DECLARE $tag AS Utf8;
            SELECT click_count FROM source_clicks WHERE tag = $tag;
            """,
            parameters={"$tag": tag},
            table="source_clicks",
        )
        for rs in result:
            for row in rs.rows:
                count = int(row.click_count)
        _execute(
            """
            DECLARE $tag AS Utf8;
            DECLARE $count AS Uint64;
            UPSERT INTO source_clicks (tag, click_count)
            VALUES ($tag, $count);
            """,
            parameters={"$tag": tag, "$count": count + 1},
            table="source_clicks",
        )
    except TableMissing:
        raise


def _source_ensure_click_row(tag):
    try:
        _execute(
            """
            DECLARE $tag AS Utf8;
            DECLARE $count AS Uint64;
            UPSERT INTO source_clicks (tag, click_count)
            VALUES ($tag, $count);
            """,
            parameters={"$tag": tag, "$count": 0},
            table="source_clicks",
        )
    except TableMissing:
        raise
    except Exception:
        LOG.exception("source_clicks bootstrap failed tag=%s", tag)


def _source_increment_start(tag):
    tag = str(tag or "").strip()
    if not parse_source_tag(tag):
        return
    count = 0
    try:
        result = _execute(
            """
            DECLARE $tag AS Utf8;
            SELECT start_count FROM source_starts WHERE tag = $tag;
            """,
            parameters={"$tag": tag},
            table="source_starts",
        )
        for rs in result:
            for row in rs.rows:
                count = int(row.start_count)
        _execute(
            """
            DECLARE $tag AS Utf8;
            DECLARE $count AS Uint64;
            UPSERT INTO source_starts (tag, start_count)
            VALUES ($tag, $count);
            """,
            parameters={"$tag": tag, "$count": count + 1},
            table="source_starts",
        )
    except TableMissing:
        return


def _source_delete_tag(tag):
    tag = str(tag or "").strip()
    if not SOURCE_TAG_RE.fullmatch(tag):
        raise ValueError("invalid_tag")
    if not _source_tag_exists(tag):
        raise ValueError("unknown_tag")
    _execute(
        """
        DECLARE $tag AS Utf8;
        DELETE FROM source_tags WHERE tag = $tag;
        """,
        parameters={"$tag": tag},
        table="source_tags",
    )
    try:
        _execute(
            """
            DECLARE $tag AS Utf8;
            DELETE FROM source_clicks WHERE tag = $tag;
            """,
            parameters={"$tag": tag},
            table="source_clicks",
        )
    except TableMissing:
        pass


def _source_list_tags_with_stats():
    tags = []
    try:
        tag_result = _execute(
            """
            SELECT tag, name, channel, campaign, created_at
            FROM source_tags
            ORDER BY created_at DESC
            LIMIT 500;
            """,
            table="source_tags",
        )
    except TableMissing:
        raise
    for rs in tag_result:
        for row in rs.rows:
            tags.append(
                {
                    "tag": str(row.tag),
                    "name": str(row.name or ""),
                    "channel": str(row.channel or ""),
                    "campaign": str(row.campaign or ""),
                    "created_at": str(row.created_at or ""),
                }
            )
    clicks_by_tag = {}
    try:
        click_result = _execute(
            "SELECT tag, click_count FROM source_clicks;",
            table="source_clicks",
        )
        for rs in click_result:
            for row in rs.rows:
                clicks_by_tag[str(row.tag)] = int(row.click_count)
    except TableMissing:
        pass
    user_rows = []
    try:
        user_result = _execute(
            "SELECT user_id, tag FROM user_sources;",
            table="user_sources",
        )
        for rs in user_result:
            for row in rs.rows:
                user_rows.append(
                    {"user_id": str(row.user_id), "tag": str(row.tag)}
                )
    except TableMissing:
        pass
    all_starts_by_tag = {}
    try:
        start_result = _execute(
            "SELECT tag, start_count FROM source_starts;",
            table="source_starts",
        )
        for rs in start_result:
            for row in rs.rows:
                all_starts_by_tag[str(row.tag)] = int(row.start_count)
    except TableMissing:
        pass
    users_by_tag = group_users_by_tag(user_rows)
    all_uids = set()
    for uids in users_by_tag.values():
        all_uids.update(uids)
    diary_cache = {}

    def has_diary(uid):
        if uid not in diary_cache:
            state = _read_state(uid) or {}
            diary_cache[uid] = len(state.get("entries") or []) >= 1
        return diary_cache[uid]

    diary_by_tag = diary_users_per_tag(users_by_tag, has_diary)
    return enrich_tags_with_stats(
        tags, clicks_by_tag, all_starts_by_tag, users_by_tag, diary_by_tag
    )


def _source_create_tag(payload):
    name = str(payload.get("name") or "").strip()[:200]
    channel = str(payload.get("channel") or "").strip().lower()
    campaign = str(payload.get("campaign") or "").strip()[:200]
    if not name:
        raise ValueError("invalid_name")
    if channel not in SOURCE_CHANNELS:
        raise ValueError("invalid_channel")
    tag = str(payload.get("tag") or "").strip()
    if tag:
        if not SOURCE_TAG_RE.fullmatch(tag):
            raise ValueError("invalid_tag")
    else:
        tag = build_tag_slug(name, channel, campaign)
    try:
        dupe = _execute(
            """
            DECLARE $tag AS Utf8;
            SELECT tag FROM source_tags WHERE tag = $tag;
            """,
            parameters={"$tag": tag},
            table="source_tags",
        )
        for rs in dupe:
            if list(rs.rows):
                raise ValueError("tag_exists")
    except TableMissing:
        raise
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    try:
        _execute(
            """
            DECLARE $tag AS Utf8;
            DECLARE $name AS Utf8;
            DECLARE $channel AS Utf8;
            DECLARE $campaign AS Utf8;
            DECLARE $created AS Utf8;
            INSERT INTO source_tags (tag, name, channel, campaign, created_at)
            VALUES ($tag, $name, $channel, $campaign, $created);
            """,
            parameters={
                "$tag": tag,
                "$name": name,
                "$channel": channel,
                "$campaign": campaign,
                "$created": now,
            },
            table="source_tags",
        )
    except Exception as exc:
        if _source_tag_exists(tag):
            raise ValueError("tag_exists") from exc
        raise
    try:
        _source_ensure_click_row(tag)
    except TableMissing:
        raise
    except Exception:
        LOG.exception("source_clicks init failed after tag create tag=%s", tag)
    return {
        "tag": tag,
        "name": name,
        "channel": channel,
        "campaign": campaign,
        "created_at": now,
        "telegram_url": telegram_start_url(tag),
    }


def _go_source_redirect(tag):
    tag = str(tag or "").strip()
    if not SOURCE_TAG_RE.fullmatch(tag):
        return _json_response(400, {"error": "invalid_tag"})
    if not _source_tag_exists(tag):
        return _json_response(404, {"error": "unknown_tag"})
    try:
        _source_increment_click(tag)
    except TableMissing:
        return _json_response(503, {"error": "source_clicks_unavailable"})
    except Exception:
        return _json_response(503, {"error": "click_failed"})
    return _redirect_response(telegram_start_url(tag))


def _imeads_check_signup(user_id):
    try:
        consents = _get_user_consents(user_id)
        if not consents.get("ai_processing") or not consents.get("updated_at"):
            return
        state = _read_state(user_id) or {}
        if not (state.get("entries") or []):
            return
        _imeads_send_once(user_id, "signup")
    except TableMissing:
        return
    except Exception:
        pass


def _touch_user_last_seen(user_id):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    _execute(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $seen AS Utf8;
        UPDATE user_profiles SET last_seen = $seen WHERE user_id = $user_id;
        """,
        parameters={"$user_id": str(user_id), "$seen": now},
        table="user_profiles",
    )


def _upsert_profile(user_id, profile, timezone_name="UTC"):
    from datetime import datetime, timezone
    profile = profile if isinstance(profile, dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    old = _execute("DECLARE $user_id AS Utf8; SELECT created_at FROM user_profiles WHERE user_id=$user_id;", parameters={"$user_id":user_id})
    created = now
    for rs in old:
        for row in rs.rows: created = str(row.created_at)
    _execute("""
        DECLARE $user_id AS Utf8; DECLARE $username AS Utf8; DECLARE $first_name AS Utf8;
        DECLARE $last_name AS Utf8; DECLARE $timezone AS Utf8; DECLARE $created AS Utf8; DECLARE $seen AS Utf8;
        UPSERT INTO user_profiles (user_id,username,first_name,last_name,timezone,created_at,last_seen)
        VALUES ($user_id,$username,$first_name,$last_name,$timezone,$created,$seen);
    """,parameters={"$user_id":user_id,"$username":str(profile.get("username") or "")[:128],
        "$first_name":str(profile.get("first_name") or "")[:128],"$last_name":str(profile.get("last_name") or "")[:128],
        "$timezone":str(timezone_name or "UTC")[:80],"$created":created,"$seen":now})


def _record_event(user_id, event_name, metadata=None):
    analytics_user_id = hmac.new(
        ANALYTICS_SECRET.encode("utf-8"),
        ("put-k-sebe-analytics:" + str(user_id)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    _execute(
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
            "$user_id": analytics_user_id,
            "$event_name": event_name,
            "$metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        },
    )


def _cors_headers():
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,PUT,DELETE,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,X-Telegram-Init-Data,X-Admin-Password,X-Route-Path,X-User-Timezone",
    }


def _json_response(status, payload):
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "isBase64Encoded": False,
    }


def _preflight_response():
    headers = _cors_headers()
    headers.pop("Content-Type", None)
    return {
        "statusCode": 204,
        "headers": headers,
        "body": "",
        "isBase64Encoded": False,
    }


def _redirect_response(location, status=302):
    return {
        "statusCode": status,
        "headers": {
            "Location": location,
            "Cache-Control": "no-store",
        },
        "body": "",
        "isBase64Encoded": False,
    }


def _request_parts(event):
    headers = event.get("headers") or {}
    headers = {str(k).lower(): str(v) for k, v in headers.items()}
    # Read the user's timezone from a query parameter to avoid custom CORS headers.
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict):
        query = {}
    raw_query = event.get("rawQueryString") or ""
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        query.setdefault(key, value)
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
    path, query = resolve_request_path(path, headers, query)
    if query.get("tz"):
        headers["x-user-timezone"] = str(query.get("tz"))[:80]
    elif query.get("timezone"):
        headers["x-user-timezone"] = str(query.get("timezone"))[:80]
    body = event.get("body") or ""
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body).decode("utf-8")
    return method, path, headers, body, query


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


def _auth_telegram_user(headers):
    """Validate Telegram init data and refresh last_seen on any authorized API call."""
    user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
    try:
        _touch_user_last_seen(user_id)
        _clear_telegram_blocked_flag(user_id)
    except TableMissing:
        raise
    except Exception:
        LOG.debug("last_seen touch failed user_id=%s", user_id)
    return user_id


def _clear_telegram_blocked_flag(user_id):
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8;
            UPDATE user_reminders SET telegram_blocked = false
            WHERE user_id = $user_id AND telegram_blocked = true;
            """,
            parameters={"$user_id": str(user_id)},
            table="user_reminders",
        )
    except TableMissing:
        raise
    except Exception:
        pass


def _read_state(user_id):
    result = _execute(
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
    _execute(
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
    practice_history = value.get("practiceHistory", [])
    personal_practices = value.get("personalPractices", [])
    chat = value.get("chat", [])
    onboarding = value.get("onboarding")

    if not isinstance(entries, list) or len(entries) > 2_000:
        raise ValueError("Invalid diary entries")
    if not isinstance(patterns, list) or len(patterns) > 300:
        raise ValueError("Invalid patterns")
    if not isinstance(completed, list) or len(completed) > 5_000:
        raise ValueError("Invalid completed practices")
    if not isinstance(practice_history, list) or len(practice_history) > 5_000:
        raise ValueError("Invalid practice history")
    if not isinstance(personal_practices, list) or len(personal_practices) > 500:
        raise ValueError("Invalid personal practices")
    if not isinstance(chat, list) or len(chat) > 300:
        raise ValueError("Invalid assistant chat history")
    if onboarding is not None and not isinstance(onboarding, dict):
        raise ValueError("Invalid onboarding data")

    clean = {
        "entries": entries,
        "patterns": patterns,
        "completed": completed,
        "practiceHistory": practice_history,
        "personalPractices": personal_practices,
        "chat": chat,
        "onboarding": onboarding,
    }
    # Keep optional application metadata, but do not accept arbitrary top-level keys.
    for key in ("settings", "meta", "aiProfile", "dailySummaryMeta"):
        if key in value and isinstance(value[key], dict):
            clean[key] = value[key]

    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BODY_BYTES:
        raise ValueError("State is too large")
    return clean


def _tribute_signature_valid(raw_body, signature):
    if not TRIBUTE_API_KEY or not signature:
        return False
    expected = hmac.new(
        TRIBUTE_API_KEY.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    supplied = signature.strip().lower()
    if supplied.startswith("sha256="):
        supplied = supplied[7:]
    return hmac.compare_digest(expected, supplied)


def _premium_status(user_id):
    from datetime import datetime, timezone, timedelta
    result = _execute(
        """
        DECLARE $user_id AS Utf8;
        SELECT purchase_id, duration_days, purchased_at
        FROM premium_purchases
        WHERE user_id = $user_id AND status = "active";
        """,
        parameters={"$user_id": user_id},
    )
    purchases = []
    for result_set in result:
        for row in result_set.rows:
            purchases.append({
                "purchase_id": str(row.purchase_id),
                "duration_days": int(row.duration_days),
                "purchased_at": str(row.purchased_at),
            })
    purchases.sort(key=lambda x: x["purchased_at"])
    now = datetime.now(timezone.utc)
    expiry = None
    for purchase in purchases:
        try:
            purchased_at = datetime.fromisoformat(
                purchase["purchased_at"].replace("Z", "+00:00")
            )
            if purchased_at.tzinfo is None:
                purchased_at = purchased_at.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        start = max(purchased_at, expiry or purchased_at)
        expiry = start + timedelta(days=purchase["duration_days"])
    paid_active = expiry is not None and expiry > now
    trial_result = _execute(
        """
        DECLARE $user_id AS Utf8;
        SELECT started_at, expires_at FROM premium_trials WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id},
    )
    trial = None
    for result_set in trial_result:
        for row in result_set.rows:
            trial = {"started_at": str(row.started_at), "expires_at": str(row.expires_at)}
    trial_active = False
    if trial:
        try:
            trial_expiry = datetime.fromisoformat(trial["expires_at"].replace("Z", "+00:00"))
            trial_active = trial_expiry > now
        except ValueError:
            trial_active = False
    active = paid_active
    premium_until = expiry if paid_active else None
    return {
        "active": active,
        "premium_until": premium_until.isoformat() if premium_until else None,
        "source": "paid" if paid_active else None,
        "trial_used": trial is not None,
        "trial_active": False,
        "paid_premium": paid_active,
    }


def _start_trial(user_id):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    started = now.isoformat()
    expires = (now + timedelta(days=7)).isoformat()
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8;
            DECLARE $started_at AS Utf8;
            DECLARE $expires_at AS Utf8;
            INSERT INTO premium_trials (user_id, started_at, expires_at)
            VALUES ($user_id, $started_at, $expires_at);
            """,
            parameters={"$user_id": user_id, "$started_at": started, "$expires_at": expires},
        )
        return _json_response(200, {"ok": True, "started": True, "active": True, "premium_until": expires, "trial_used": True})
    except Exception:
        status = _premium_status(user_id)
        if status.get("trial_used"):
            return _json_response(409, {"ok": False, "error": "trial_already_used", **status})
        raise


def _handle_tribute_webhook(raw_body, signature):
    if not _tribute_signature_valid(raw_body, signature):
        return _json_response(401, {"error": "invalid_signature"})
    try:
        event = json.loads(raw_body.decode("utf-8"))
        if not isinstance(event, dict):
            raise ValueError("Invalid webhook event")
        name = event.get("name")

        # Tribute's dashboard test request is not a purchase/refund event.
        # Acknowledge signed non-entitlement events without requiring purchase fields.
        if name not in ("new_digital_product", "digital_product_refunded"):
            return _json_response(200, {"ok": True, "ignored": True})

        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Invalid webhook payload")
        purchase_id = str(payload.get("purchase_id", "")).strip()
        if not purchase_id or purchase_id == "None":
            raise ValueError("Missing purchase_id")

        if name == "new_digital_product":
            product_id = str(payload.get("product_id", ""))
            duration = TRIBUTE_PRODUCTS.get(product_id)
            user_id = str(int(payload.get("telegram_user_id", 0)))
            purchased_at = str(payload.get("purchase_created_at") or event.get("created_at") or "")
            if not duration or user_id == "0" or not purchased_at:
                raise ValueError("Unknown product or missing purchase data")
            existing = _execute(
                """
                DECLARE $purchase_id AS Utf8;
                SELECT purchase_id FROM premium_purchases WHERE purchase_id = $purchase_id;
                """,
                parameters={"$purchase_id": purchase_id},
            )
            for result_set in existing:
                if result_set.rows:
                    return _json_response(200, {"ok": True, "duplicate": True})
            _execute(
                """
                DECLARE $purchase_id AS Utf8;
                DECLARE $user_id AS Utf8;
                DECLARE $product_id AS Utf8;
                DECLARE $duration_days AS Uint32;
                DECLARE $purchased_at AS Utf8;
                INSERT INTO premium_purchases
                    (purchase_id, user_id, product_id, duration_days, purchased_at, status)
                VALUES
                    ($purchase_id, $user_id, $product_id, $duration_days, $purchased_at, "active");
                """,
                parameters={
                    "$purchase_id": purchase_id,
                    "$user_id": user_id,
                    "$product_id": product_id,
                    "$duration_days": duration,
                    "$purchased_at": purchased_at,
                },
            )
            amount = TRIBUTE_PRODUCT_AMOUNT_RUB.get(product_id)
            if amount:
                try:
                    _imeads_send_once(user_id, "purchase", amount, "RUB")
                except Exception:
                    pass
            return _json_response(200, {"ok": True})

        if name == "digital_product_refunded":
            existing = _execute(
                """
                DECLARE $purchase_id AS Utf8;
                SELECT purchase_id FROM premium_purchases WHERE purchase_id = $purchase_id;
                """,
                parameters={"$purchase_id": purchase_id},
            )
            found = any(result_set.rows for result_set in existing)
            if not found:
                return _json_response(503, {"error": "purchase_not_found_yet"})
            _execute(
                """
                DECLARE $purchase_id AS Utf8;
                UPDATE premium_purchases
                SET status = "refunded"
                WHERE purchase_id = $purchase_id;
                """,
                parameters={"$purchase_id": purchase_id},
            )
            return _json_response(200, {"ok": True})

        return _json_response(200, {"ok": True, "ignored": True})
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return _json_response(400, {"error": str(exc)})


def _deepseek_chat(messages, max_tokens=3000, timeout=55, reasoning_effort="high", thinking_enabled=True):
    import urllib.request
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if thinking_enabled:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = reasoning_effort
    else:
        payload["thinking"] = {"type": "disabled"}
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + DEEPSEEK_API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _deepseek_choice_content(data):
    choices = data.get("choices") or []
    message = choices[0].get("message") if choices else {}
    return str((message or {}).get("content") or "").strip()


def _deepseek_finish_reason(data):
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("finish_reason") or "")


def _analysis_looks_truncated(text, finish_reason):
    if finish_reason == "length":
        return True
    text = (text or "").rstrip()
    if len(text) < 40:
        return False
    return text[-1].isalnum() and text[-1] not in "»"


def _trim_analysis_to_last_sentence(text):
    import re
    text = (text or "").rstrip()
    if not text:
        return text
    if text[-1] in ".!?»":
        return text
    spans = [m.start() for m in re.finditer(r"[.!?»](?:\s|$)", text)]
    if spans:
        return text[: spans[-1] + 1].strip()
    return text


def _parse_assistant_suggestions(text, max_len=160):
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("0123456789.)-• «»")
        if len(line) > 3:
            lines.append(line[:max_len])
    if len(lines) < 2:
        blob = str(text or "").strip()
        if blob:
            parts = [p.strip().lstrip("«").rstrip("»") for p in blob.replace(";", "\n").split("\n") if p.strip()]
            lines.extend(p[:max_len] for p in parts)
    return lines[:3]


def _hint_inflight_claim_key(step, field):
    return f"hint-inflight:{step}:{field}"


def _record_journal_funnel_event(user_id, event_kind, step_number, created_at):
    _execute(
        """
        DECLARE $event_id AS Utf8;
        DECLARE $user_id AS Utf8;
        DECLARE $event_kind AS Utf8;
        DECLARE $step_number AS Int32;
        DECLARE $created_at AS Utf8;
        INSERT INTO journal_funnel_events (
            event_id, user_id, event_kind, step_number, created_at
        )
        VALUES ($event_id, $user_id, $event_kind, $step_number, $created_at);
        """,
        parameters={
            "$event_id": str(uuid.uuid4()),
            "$user_id": str(user_id),
            "$event_kind": str(event_kind)[:40],
            "$step_number": int(step_number),
            "$created_at": str(created_at)[:40],
        },
        table="journal_funnel_events",
    )


def _deepseek_assistant_hint(user_id, payload, timezone_name="UTC"):
    from datetime import datetime, timezone

    quota = _assistant_quota_status(user_id, timezone_name)
    if quota.get("exhausted"):
        return _assistant_quota_block_response(quota)
    denied = _require_ai_processing(user_id)
    if denied:
        return denied
    if not DEEPSEEK_API_KEY:
        return _json_response(503, {"error": "deepseek_not_configured"})
    step = str(payload.get("step") or "")
    field = str(payload.get("field") or "")
    if step == "4":
        field = "against"
    thought = str(payload.get("thought") or "")[:1200]
    for_text = str(payload.get("for_text") or "")[:1200]
    against = str(payload.get("against") or "")[:1200]
    context = str(payload.get("context") or "")[:2400]
    max_tokens = 220
    suggest_max_len = 160
    if step == "5":
        field = "balanced"
    system = (
        "Ты — персональный ассистент приложения ПЕРЕСМОТР. "
        "Отвечай по-русски, без markdown и нумерации."
    )
    if step == "4":
        system += (
            " На шаге «факты против» дай 3 короткие подсказки-аргументы против автоматической мысли, "
            "не факты «за» и не новую сбалансированную формулировку."
        )
        user_message = (
            f"Исходная мысль: {thought}\n"
            f"Уже написано: {context}\n"
            "Верни ровно 3 подсказки, каждая с новой строки, до 120 символов."
        )
    elif step == "5":
        system += (
            " На шаге более сбалансированной мысли верни ровно 3 готовые альтернативные "
            "формулировки новой мысли от первого лица. Это готовые фразы мыслей, а не советы, "
            "вопросы или инструкции. Учитывай исходную автоматическую мысль и факты за/против."
        )
        user_message = (
            f"Исходная автоматическая мысль: {thought}\n"
            f"Факты за мысль: {for_text or '(пока пусто)'}\n"
            f"Факты против мысли: {against or '(пока пусто)'}\n"
            "Верни ровно 3 формулировки новой сбалансированной мысли, каждая с новой строки, "
            "до 160 символов, без кавычек и нумерации."
        )
        max_tokens = 320
        suggest_max_len = 180
    else:
        system += " Дай 3 короткие подсказки."
        user_message = (
            f"Шаг мастера: {step}\nПоле: {field}\nИсходная мысль: {thought}\n"
            f"Уже написано пользователем: {context}\n"
            "Верни 3 подсказки, каждая с новой строки, до 120 символов."
        )
    inflight_key = _hint_inflight_claim_key(step, field)
    created = datetime.now(timezone.utc).isoformat()
    if not _claim_summary_notification(user_id, inflight_key, created):
        return _json_response(
            409,
            {
                "error": "assistant_hint_in_progress",
                "message": "Подсказка уже готовится. Подожди несколько секунд.",
                **_assistant_quota_status(user_id, timezone_name),
            },
        )
    try:
        data = _deepseek_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_message}],
            max_tokens=max_tokens,
            timeout=25,
            reasoning_effort="low",
            thinking_enabled=False,
        )
        suggestions = _parse_assistant_suggestions(
            _deepseek_choice_content(data), max_len=suggest_max_len
        )
        if step == "5":
            fallbacks = [
                "Я не знаю всей картины: ситуация может значить не только одно.",
                "Задержка ответа ещё не доказывает, что со мной что-то не так.",
                "Мне помогает помнить и факты «за», и факты «против» этой мысли.",
            ]
            while len(suggestions) < 3:
                suggestions.append(fallbacks[len(suggestions)])
            suggestions = suggestions[:3]
        elif len(suggestions) < 2:
            if step == "4":
                suggestions = [
                    "Что бы сказал близкий друг, если бы услышал эту мысль?",
                    "Были ли случаи, когда похожая мысль не подтвердилась?",
                    "Что ты знаешь точно, а что пока предполагаешь?",
                ]
            else:
                suggestions = [
                    "Опиши, что произошло, без оценок себя.",
                    "Отдели факты от предположений.",
                ]
        request_key = f"{step}:{field}:{uuid.uuid4().hex[:8]}"
        _record_chargeable_assistant_use(user_id, "assistant_hint", request_key, created)
        quota = _assistant_quota_status(user_id, timezone_name)
        return _json_response(200, {"ok": True, "suggestions": suggestions[:3], **quota})
    except Exception:
        LOG.exception("assistant hint failed")
        return _json_response(
            502,
            {"error": "assistant_hint_failed", "message": "Не удалось получить подсказку. Попробуй позже."},
        )
    finally:
        _release_summary_notification(user_id, inflight_key)


def _diary_entry_quality_error(entry):
    """Reject empty or fragmentary input before spending tokens on a personal analysis."""
    import re
    situation = str(entry.get("situation") or "").strip()
    thought = str(entry.get("thought") or "").strip()
    core = (situation + " " + thought).strip()
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", core)
    meaningful = [word for word in words if len(word) >= 3]
    unique = {word.casefold() for word in meaningful}
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", core)
    if len(core) < 28 or len(meaningful) < 4 or len(unique) < 3 or len(letters) < 20:
        return ("Пока не получается разобрать запись: в ситуации и автоматической мысли "
                "слишком мало понятного контекста. Добавь несколько предложений: что именно "
                "произошло и какая мысль возникла у тебя. После этого можно повторить анализ.")
    return None


def _deepseek_analyze_entry(user_id, entry_id, timezone_name="UTC"):
    from datetime import datetime, timezone
    import urllib.error
    stage = "load_user_state"
    quota = _assistant_quota_status(user_id, timezone_name)
    state = _read_state(user_id)
    if not isinstance(state, dict):
        return _json_response(404, {"error": "user_state_not_found"})
    entries = state.get("entries") or []
    entry = next((item for item in entries if str(item.get("id")) == entry_id), None)
    if not isinstance(entry, dict):
        return _json_response(404, {"error": "entry_not_found"})
    quality_error = _diary_entry_quality_error(entry)
    if quality_error:
        return _json_response(422, {"error": "entry_not_clear", "message": quality_error})
    # New cache namespace also invalidates analyses produced from malformed test entries.
    cache_entry_id = "deepseek-flash:v4-quality-gate:" + entry_id
    stage = "read_analysis_cache"
    cached = _execute(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $entry_id AS Utf8;
        SELECT analysis_text, input_tokens, output_tokens, created_at
        FROM ai_analyses WHERE user_id = $user_id AND entry_id = $entry_id;
        """,
        parameters={"$user_id": user_id, "$entry_id": cache_entry_id},
    )
    for result_set in cached:
        for row in result_set.rows:
            return _json_response(200, {
                "ok": True, "cached": True, "analysis": str(row.analysis_text),
                "usage": {"input_tokens": int(row.input_tokens), "output_tokens": int(row.output_tokens)},
                "created_at": str(row.created_at),
                **quota,
            })

    if quota.get("exhausted"):
        return _assistant_quota_block_response(quota)
    stage = "prepare_prompt"
    fields = [
        ("Ситуация", "situation"), ("Эмоция", "emotion"),
        ("Интенсивность эмоции (0–10)", "intensity"),
        ("Автоматическая мысль", "thought"), ("Факты за мысль", "forText"),
        ("Факты против мысли", "against"), ("Более сбалансированный взгляд", "balanced"),
        ("Следующий небольшой шаг", "action"),
    ]
    entry_text = "\n".join(label + ": " + str(entry.get(key, ""))[:1800] for label, key in fields)
    onboarding = state.get("onboarding") if isinstance(state.get("onboarding"), dict) else {}
    user_context = {
        "что_сейчас_беспокоит": str(onboarding.get("main_issue") or "")[:1200],
        "важные_сферы": onboarding.get("areas") or [],
        "знакомые_мысли": onboarding.get("thoughts") or [],
        "обычные_реакции": onboarding.get("reactions") or [],
        "чего_хочет_изменить": str(onboarding.get("desired_changes") or "")[:1200],
    }
    system_prompt = (
        "Ты — Персональный аналитик приложения «Путь к себе». "
        "Помогай человеку в рамках навыков КПТ и бережной саморефлексии. "
        "Отвечай по-русски, уважительно, конкретно и без шаблонных комплиментов. "
        "Не ставь диагнозов, не делай выводов о личности по одной записи, не выдавай гипотезы за факты. "
        "Не навязывай позитивное мышление. Если данные выглядят как бессвязный набор слов, случайные символы, "
        "обрывки или не позволяют понять событие и мысль, НЕ пытайся угадывать смысл: скажи, что запись "
        "не удалось расшифровать, попроси переписать её и НЕ предлагай когнитивные искажения или паттерны. "
        "Если данных недостаточно, прямо скажи об этом. "
        "Не ищи когнитивное искажение там, где его нет. "
        "Структура ответа: 1) Что видно в ситуации. "
        "2) Возможная автоматическая мысль и когнитивное искажение (только если есть основания; "
        "иначе напиши, что явного искажения не видно). "
        "3) Что запись может упускать или какие вопросы стоит себе задать. "
        "4) Более точный, сбалансированный взгляд. 5) Один небольшой практический шаг. "
        "6) Гипотеза паттерна для наблюдения. Не требуй доказательств повторения сценария в нескольких записях: "
        "одна запись может дать предварительную гипотезу о возможной привычной реакции, убеждении или способе интерпретации. "
        "Ищи её в автоматической мысли, эмоции, фактах за/против, сбалансированном взгляде и следующем шаге. "
        "Например, глобальный вывод о себе из одного отказа может указывать на склонность обобщать; "
        "предположение о чужой негативной реакции без подтверждения — на ожидание отвержения или чтение мыслей. "
        "Это лишь примеры, не назначай их автоматически: опирайся на конкретную запись. "
        "Если есть разумная рабочая гипотеза, обязательно закончи ответ отдельной строкой строго формата "
        "'Гипотеза паттерна: формулировка от первого лица | краткое описание'. "
        "КРИТИЧЕСКИ ВАЖНО: название паттерна должно быть понятным человеку тезисом о себе или о мире, "
        "который он может узнать как свою внутреннюю установку и при желании переписать. "
        "Формулируй коротко, от первого лица, по возможности близко к словам пользователя. "
        "НЕ называй паттерн профессиональным термином или описанием механизма вроде "
        "'Глобальный ярлык из одного события', 'Сверхобобщение', 'Страх отвержения'. "
        "Вместо этого пиши конкретную внутреннюю установку: например, "
        "'Я непривлекательный и никому не нужен', 'Если мне не отвечают, значит, я неинтересен', "
        "'Если я ошибаюсь, значит, я неудачник'. Это примеры формы, а не готовые выводы — "
        "выводи формулировку только из конкретной записи. "
        "В кратком описании поясни, в какой ситуации эта мысль включается и как человек связывает событие с выводом о себе. "
        "Не превращай ситуативную мысль в постоянное убеждение: помечай её как гипотезу для проверки. "
        "Пиши 'Гипотеза паттерна: нет достаточных данных' ТОЛЬКО если в самой записи нет ни одной мысли, реакции "
        "или поведения, из которых можно осторожно вывести такую формулировку. "
        "Если по конкретной записи уместно предложить одно небольшое действие, закончи ответ отдельной строкой строго формата 'Микро-практика: короткое название | конкретная инструкция на 1–10 минут | длительность'. Практика должна вытекать из записи, быть выполнимой самостоятельно и не повторять общий шаблон без оснований. Если подходящего действия нет, эту строку не добавляй. Не используй Markdown-звёздочки. Не более 250 слов; по 2–3 коротких предложения на каждый пункт структуры. Заканчивай каждый пункт полным предложением. Это поддержка для самонаблюдения, не психотерапия и не медицинская помощь."
    )
    user_message = (
        "Контекст из анкеты (помогает учитывать цели пользователя, но не является доказательством):\n"
        + json.dumps(user_context, ensure_ascii=False)
        + "\n\nДанные текущей записи:\n"
        + entry_text
    )
    try:
        stage = "request_deepseek"
        data = _deepseek_chat(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}],
            max_tokens=6000,
            timeout=90,
            reasoning_effort="low",
            thinking_enabled=True,
        )
        stage = "parse_model_response"
        finish_reason = _deepseek_finish_reason(data)
        analysis = _deepseek_choice_content(data)
        truncated = _analysis_looks_truncated(analysis, finish_reason)
        if truncated:
            stage = "request_deepseek_retry"
            retry_data = _deepseek_chat(
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_message
                        + "\n\nОтветь компактно (~220 слов), без рассуждений вслух: 2–3 предложения на каждый пункт, каждый пункт закончи полным предложением.",
                    },
                ],
                max_tokens=6000,
                timeout=90,
                thinking_enabled=False,
            )
            data = retry_data
            finish_reason = _deepseek_finish_reason(data)
            analysis = _deepseek_choice_content(data)
            truncated = _analysis_looks_truncated(analysis, finish_reason)
        if truncated:
            analysis = _trim_analysis_to_last_sentence(analysis)
            usage = data.get("usage") or {}
            LOG.info(
                "entry analysis truncated user_id=%s finish_reason=%s completion_tokens=%s",
                user_id,
                finish_reason,
                int(usage.get("completion_tokens", 0)),
            )
        if not analysis:
            LOG.error("DeepSeek analysis returned no output")
            return _json_response(502, {"error": "empty_model_response"})
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        created = datetime.now(timezone.utc).isoformat()
        stage = "save_analysis_to_ydb"
        _save_ai_result(user_id, cache_entry_id, analysis,
                        {"input_tokens": input_tokens, "output_tokens": output_tokens}, created)
        return _json_response(200, {
            "ok": True, "cached": False, "analysis": analysis,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "created_at": created,
        })
    except urllib.error.HTTPError as exc:
        try:
            provider_data = json.loads(exc.read(2000).decode("utf-8", errors="replace"))
            provider_error = provider_data.get("error") or {}
            provider_message = str(provider_error.get("message", "DeepSeek request rejected"))[:500]
            provider_code = str(provider_error.get("code") or provider_error.get("type") or "provider_error")[:100]
        except Exception:
            provider_message = "DeepSeek returned an unreadable error response"
            provider_code = "provider_error"
        LOG.error("DeepSeek HTTP error status=%s code=%s message=%s", exc.code, provider_code, provider_message)
        return _json_response(502, {
            "error": "deepseek_provider_error",
            "provider_status": int(exc.code),
            "provider_code": provider_code,
            "detail": provider_message,
        })
    except urllib.error.URLError as exc:
        LOG.error("DeepSeek network error reason=%s", str(exc.reason)[:300])
        return _json_response(502, {"error": "deepseek_network_error", "detail": str(exc.reason)[:300]})
    except TableMissing:
        raise
    except Exception as exc:
        safe_detail = str(exc).replace("\\n", " ")[:500]
        LOG.exception("AI analysis failed stage=%s type=%s detail=%s", stage, type(exc).__name__, safe_detail)
        return _json_response(502, {
            "error": "deepseek_analysis_failed",
            "stage": stage,
            "exception_type": type(exc).__name__,
            "detail": stage + ": " + type(exc).__name__ + ": " + safe_detail,
        })



def _resolve_timezone(headers, query=None, payload=None):
    q = query if isinstance(query, dict) else {}
    if isinstance(payload, dict):
        for key in ("timezone", "tz"):
            if payload.get(key):
                return str(payload.get(key))[:80]
    if headers.get("x-user-timezone"):
        return str(headers.get("x-user-timezone"))[:80]
    if q.get("tz"):
        return str(q.get("tz"))[:80]
    if q.get("timezone"):
        return str(q.get("timezone"))[:80]
    return "UTC"


def _safe_user_timezone(name):
    from datetime import timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(str(name or "UTC")[:80])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.utc


def _created_at_is_local_day(value, day_key, user_tz):
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(user_tz).date().isoformat() == day_key
    except (ValueError, TypeError):
        return False


def _daily_ai_usage(user_id, day_key, user_tz):
    analyzed_entries = set()
    summary_exists = False
    result = _execute("""
        DECLARE $user_id AS Utf8;
        SELECT feature, request_key, created_at FROM ai_usage_events WHERE user_id = $user_id;
        """, parameters={"$user_id": user_id})
    for result_set in result:
        for row in result_set.rows:
            feature, request_key, created_at = str(row.feature), str(row.request_key), str(row.created_at)
            if not _created_at_is_local_day(created_at, day_key, user_tz):
                continue
            if feature == "daily_summary":
                summary_exists = True
            elif feature == "analyze_entry":
                analyzed_entries.add(request_key)
    legacy = _execute("""
        DECLARE $user_id AS Utf8;
        SELECT entry_id, created_at FROM ai_analyses WHERE user_id = $user_id;
        """, parameters={"$user_id": user_id})
    for result_set in legacy:
        for row in result_set.rows:
            entry_id, created_at = str(row.entry_id), str(row.created_at)
            if not _created_at_is_local_day(created_at, day_key, user_tz):
                continue
            if entry_id.startswith("daily-summary:"):
                summary_exists = True
            elif not entry_id.startswith("onboarding-profile:"):
                analyzed_entries.add(entry_id.rsplit(":", 1)[-1])
    return len(analyzed_entries), summary_exists


def _lifetime_assistant_used(user_id):
    result = _execute(
        """
        DECLARE $user_id AS Utf8;
        SELECT lifetime_used FROM assistant_quota WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id},
    )
    for result_set in result:
        for row in result_set.rows:
            return int(row.lifetime_used)
    return 0


def _daily_assistant_charges(user_id, day_key, user_tz):
    count = 0
    result = _execute(
        """
        DECLARE $user_id AS Utf8;
        SELECT feature, created_at FROM ai_usage_events WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id},
    )
    for result_set in result:
        for row in result_set.rows:
            feature = str(row.feature)
            if feature not in CHARGE_FEATURES:
                continue
            if _created_at_is_local_day(str(row.created_at), day_key, user_tz):
                count += 1
    return count


def _bump_lifetime_assistant_used(user_id):
    used = _lifetime_assistant_used(user_id) + 1
    _execute(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $lifetime_used AS Uint32;
        UPSERT INTO assistant_quota (user_id, lifetime_used)
        VALUES ($user_id, $lifetime_used);
        """,
        parameters={
            "$user_id": user_id,
            "$lifetime_used": ydb.TypedValue(used, ydb.PrimitiveType.Uint32),
        },
    )
    return used


def _assistant_quota_status(user_id, timezone_name="UTC"):
    from datetime import datetime

    premium = _premium_status(user_id)
    paid = bool(premium.get("paid_premium"))
    user_tz = _safe_user_timezone(timezone_name)
    day_key = datetime.now(user_tz).date().isoformat()
    lifetime_used = _lifetime_assistant_used(user_id)
    daily_used = _daily_assistant_charges(user_id, day_key, user_tz)
    _, summary_done = _daily_ai_usage(user_id, day_key, user_tz)
    return quota_payload(bool(premium.get("active")), paid, lifetime_used, daily_used, summary_done)


def _assistant_quota_block_response(status):
    if status["mode"] == "daily":
        return _json_response(
            429,
            {
                "error": "daily_assistant_limit",
                "message": "На сегодня использованы все подсказки персонального ассистента.",
                **status,
            },
        )
    return _json_response(
        429,
        {
            "error": "lifetime_assistant_limit",
            "message": "Бесплатные подсказки ассистента закончились.",
            **status,
        },
    )


def _record_chargeable_assistant_use(user_id, feature, request_key, created):
    _execute(
        """
        DECLARE $event_id AS Utf8;
        DECLARE $user_id AS Utf8;
        DECLARE $feature AS Utf8;
        DECLARE $request_key AS Utf8;
        DECLARE $created_at AS Utf8;
        INSERT INTO ai_usage_events (event_id, user_id, feature, request_key, created_at)
        VALUES ($event_id, $user_id, $feature, $request_key, $created_at);
        """,
        parameters={
            "$event_id": str(uuid.uuid4()),
            "$user_id": user_id,
            "$feature": feature,
            "$request_key": request_key,
            "$created_at": created,
        },
    )
    premium = _premium_status(user_id)
    if not premium.get("paid_premium"):
        return _bump_lifetime_assistant_used(user_id)
    return _lifetime_assistant_used(user_id)


def _save_ai_result(user_id, cache_key, text_value, usage, created):
    consent = _get_user_consents(user_id)
    feature = "daily_summary" if cache_key.startswith("daily-summary:") else ("onboarding" if cache_key.startswith("onboarding-profile:") else "analyze_entry")
    request_key = cache_key.rsplit(":", 1)[-1]
    if feature in CHARGE_FEATURES:
        _record_chargeable_assistant_use(user_id, feature, request_key, created)
    else:
        _execute(
            """
            DECLARE $event_id AS Utf8;
            DECLARE $user_id AS Utf8;
            DECLARE $feature AS Utf8;
            DECLARE $request_key AS Utf8;
            DECLARE $created_at AS Utf8;
            INSERT INTO ai_usage_events (event_id, user_id, feature, request_key, created_at)
            VALUES ($event_id, $user_id, $feature, $request_key, $created_at);
            """,
            parameters={
                "$event_id": str(uuid.uuid4()),
                "$user_id": user_id,
                "$feature": feature,
                "$request_key": request_key,
                "$created_at": created,
            },
        )
    if not consent["ai_history_storage"]:
        return
    _execute("""
        DECLARE $user_id AS Utf8;
        DECLARE $entry_id AS Utf8;
        DECLARE $analysis_text AS Utf8;
        DECLARE $input_tokens AS Uint64;
        DECLARE $output_tokens AS Uint64;
        DECLARE $created_at AS Utf8;
        UPSERT INTO ai_analyses (user_id, entry_id, analysis_text, input_tokens, output_tokens, created_at)
        VALUES ($user_id, $entry_id, $analysis_text, $input_tokens, $output_tokens, $created_at);
        """, parameters={"$user_id": user_id, "$entry_id": cache_key, "$analysis_text": text_value,
            "$input_tokens": ydb.TypedValue(max(0, int(usage.get("input_tokens", 0))), ydb.PrimitiveType.Uint64),
            "$output_tokens": ydb.TypedValue(max(0, int(usage.get("output_tokens", 0))), ydb.PrimitiveType.Uint64), "$created_at": created})

def _get_previous_daily_summary(user_id, timezone_name="UTC"):
    from datetime import datetime, timedelta
    user_tz = _safe_user_timezone(timezone_name)
    day_key = (datetime.now(user_tz).date() - timedelta(days=1)).isoformat()
    consent = _get_user_consents(user_id)
    if not consent.get("ai_history_storage"):
        return _json_response(403, {"error":"summary_history_not_enabled",
            "message":"Для просмотра прошлых сводок нужно включить хранение истории AI."})
    cache_key = "daily-summary:" + day_key
    rows = _execute("""
        DECLARE $user_id AS Utf8;
        DECLARE $entry_id AS Utf8;
        SELECT analysis_text, created_at FROM ai_analyses
        WHERE user_id=$user_id AND entry_id=$entry_id;
        """, parameters={"$user_id":user_id, "$entry_id":cache_key})
    for result_set in rows:
        for row in result_set.rows:
            return _json_response(200, {"ok":True, "day":day_key,
                "summary":str(row.analysis_text), "created_at":str(row.created_at)})
    return _json_response(404, {"error":"previous_summary_not_found",
        "message":"Сохранённая сводка за предыдущий день не найдена."})


def _deepseek_daily_summary(user_id, timezone_name="UTC", force=False):
    from datetime import datetime, timezone
    import urllib.error
    # Daily summaries are available on every tier, exactly once per local calendar day.
    # The force argument is intentionally ignored: Premium never bypasses the daily lock.
    user_tz = _safe_user_timezone(timezone_name)
    now = datetime.now(timezone.utc)
    day_key = now.astimezone(user_tz).date().isoformat()
    cache_key = "daily-summary:" + day_key
    _, summary_exists = _daily_ai_usage(user_id, day_key, user_tz)
    if summary_exists:
        cached_once = _execute("""
            DECLARE $user_id AS Utf8; DECLARE $entry_id AS Utf8;
            SELECT analysis_text, created_at FROM ai_analyses WHERE user_id=$user_id AND entry_id=$entry_id;
        """,parameters={"$user_id":user_id,"$entry_id":cache_key})
        for rs in cached_once:
            for row in rs.rows:
                return _json_response(200,{"ok":True,"cached":True,"summary":str(row.analysis_text),"created_at":str(row.created_at)})
        return _json_response(409,{"error":"daily_summary_already_used","message":"Итоги за сегодня уже подведены. Повторный обзор недоступен."})
    state = _read_state(user_id)
    if not isinstance(state, dict):
        return _json_response(404, {"error": "user_state_not_found"})
    todays = []
    for item in state.get("entries") or []:
        if not isinstance(item, dict):
            continue
        try:
            entry_date = datetime.fromisoformat(str(item.get("date", "")).replace("Z", "+00:00"))
            if entry_date.tzinfo is None:
                entry_date = entry_date.replace(tzinfo=timezone.utc)
            if entry_date.astimezone(user_tz).date().isoformat() == day_key:
                todays.append(item)
        except (ValueError, TypeError):
            continue
    practice_today = []
    for practice in state.get("practiceHistory") or []:
        if not isinstance(practice, dict):
            continue
        try:
            completed = datetime.fromisoformat(str(practice.get("completedAt", "")).replace("Z", "+00:00"))
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            if completed.astimezone(user_tz).date().isoformat() == day_key:
                practice_today.append(practice)
        except (ValueError, TypeError):
            continue
    if not todays and not practice_today:
        return _json_response(400, {"error": "no_entries_today", "message": "Сегодня пока нет записей или выполненных практик для сводки."})
    import hashlib
    # Ignore empty, fragmentary or gibberish entries. A practice-only day is still
    # meaningful input when the user has recorded what they completed.
    meaningful_entries = [item for item in todays if not _diary_entry_quality_error(item)]
    if todays and not meaningful_entries and not practice_today:
        return _json_response(422, {"error":"no_meaningful_entries",
            "message":"В сегодняшних записях пока недостаточно понятного контекста для сводки."})
    todays = meaningful_entries
    # A unique YDB primary key serializes manual and scheduled requests for this user/day.
    try:
        _execute("""
            DECLARE $user_id AS Utf8; DECLARE $day_key AS Utf8; DECLARE $created_at AS Utf8;
            INSERT INTO daily_summary_claims (user_id, day_key, created_at)
            VALUES ($user_id, $day_key, $created_at);
        """, parameters={"$user_id": user_id, "$day_key": day_key, "$created_at": now.isoformat()})
    except TableMissing:
        raise
    except Exception:
        return _json_response(409, {"error":"daily_summary_already_used",
            "message":"Итоги за сегодня уже формируются или уже подведены."})
    snippets = []
    for i, entry in enumerate(todays[-30:], 1):
        fields = [("Ситуация","situation"),("Эмоция","emotion"),("Интенсивность","intensity"),
                  ("Автоматическая мысль","thought"),("Факты за","forText"),("Факты против","against"),
                  ("Сбалансированный взгляд","balanced"),("Следующий шаг","action")]
        snippets.append("Запись " + str(i) + ":\n" + "\n".join(
            label + ": " + str(entry.get(key, ""))[:900] for label, key in fields))
    onboarding = state.get("onboarding") if isinstance(state.get("onboarding"), dict) else {}
    user_context = {
        "что_сейчас_беспокоит": str(onboarding.get("main_issue") or "")[:1800],
        "важные_сферы": onboarding.get("areas") or [],
        "знакомые_мысли": onboarding.get("thoughts") or [],
        "обычные_реакции": onboarding.get("reactions") or [],
        "чего_хочет_изменить": str(onboarding.get("desired_changes") or "")[:1800],
    }
    system_prompt = (
        "Ты — персональный аналитик продукта «Путь к себе». Твоя задача — помогать человеку "
        "лучше понимать свои мысли, эмоции, поведение и повторяющиеся сценарии через бережную "
        "саморефлексию и инструменты, близкие к КПТ. Ты не врач и не психотерапевт. "
        "Пиши по-русски, на «ты», спокойно, уважительно, ясно и конкретно. Не используй "
        "канцелярит, эзотерику, мотивационные лозунги, пустые комплименты и категоричные ярлыки. "
        "Не ставь диагнозов, не делай выводов о личности и не приписывай человеку травмы, "
        "расстройства, мотивы или намерения других людей. Не навязывай позитивное мышление. "
        "Главная цель — не успокоить любой ценой, а помочь точнее увидеть ситуацию и отделить "
        "наблюдаемые факты от предположений. "
        "Анкета пользователя — только фон для понимания его целей и знакомых трудностей; "
        "она не является доказательством того, что конкретный паттерн проявился сегодня. "
        "Опирайся прежде всего на сегодняшние записи. Не выдумывай факты, повторения, причинно-следственные "
        "связи и прогресс. Если повторение видно лишь в одной записи, называй его предварительной "
        "гипотезой, а не устойчивым паттерном. Если данных мало или записи противоречат друг другу, "
        "прямо обозначь неопределённость. Не анализируй бессвязный текст и не пытайся угадывать "
        "смысл непонятных фрагментов. "
        "Структура сводки: 1) короткая картина дня без оценок; 2) заметные связи между ситуациями, "
        "автоматическими мыслями, эмоциями и действиями; 3) повторяющиеся темы — только если они "
        "действительно встречаются в нескольких записях, с указанием номеров записей; "
        "4) что человек уже сделал полезного или что стало яснее — только если это видно из записей; "
        "5) один точный вопрос для дальнейшего наблюдения; 6) до трёх гипотез паттернов; "
        "Для каждой гипотезы укажи конкретные номера записей, на которых она основана, и "
        "отдельно отметь, если основание пока только одно. Формулируй гипотезы человеческим языком, "
        "по возможности от первого лица, не профессиональными терминами. Не предлагай паттерн, "
        "если нет достаточных оснований. Не предлагай автоматически добавить гипотезу в профиль. " 
        "7) до трёх персональных микро-практик на завтра. Учитывай выполненные, пропущенные и отклонённые задания, " 
        "а также обратную связь о сложности и пользе, если она есть. Каждая практика должна быть конкретной, " 
        "посильной и связанной с сегодняшними наблюдениями. Не повторяй задание, которое человек уже выполнил, " 
        "если нет причины повторить его. Если данных мало, честно предложи не более одной простой практики. " 
        "Для каждого задания добавь отдельную строку строго формата 'Практика на завтра: название | инструкция | длительность'. " 
        "Не добавляй такие строки для отклонённых или уже выполненных без причины заданий. "
        "Не более 500 слов. Это инструмент самонаблюдения, не медицинская помощь."
    )
    try:
        data = _deepseek_chat([{"role":"system","content":system_prompt},
            {"role":"user","content":"Контекст из анкеты (ориентир для персонализации, не доказательство):\n"+json.dumps(user_context, ensure_ascii=False)+
                "\n\nЗаписи за "+day_key+":\n\n"+"\n\n".join(snippets)+
                "\n\nПодтверждённые паттерны пользователя (контекст, не готовый вывод): "+json.dumps(
                    [{"name":p.get("name"),"detail":p.get("detail")} for p in (state.get("patterns") or [])
                     if isinstance(p,dict) and p.get("status")=="confirmed"][:20], ensure_ascii=False)+
                "\n\nИстория выполненных и пропущенных практик: "+json.dumps(
                    [p for p in (state.get("practiceHistory") or []) if isinstance(p,dict)][-20:],
                    ensure_ascii=False)[:5000]+
                "\n\nПерсональные практики и их статусы: "+json.dumps(
                    [p for p in (state.get("personalPractices") or []) if isinstance(p,dict)][-30:],
                    ensure_ascii=False)[:6000]}],
            max_tokens=4500, timeout=55)
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices else {}
        summary = str((message or {}).get("content") or "").strip()
        if not summary:
            return _json_response(502, {"error":"empty_model_response"})
        u = data.get("usage") or {}
        usage = {"input_tokens":int(u.get("prompt_tokens",0)),"output_tokens":int(u.get("completion_tokens",0))}
        created = now.isoformat()
        _save_ai_result(user_id, cache_key, summary, usage, created)
        _record_event(user_id, "daily_summary_created")
        return _json_response(200, {"ok":True,"cached":False,"summary":summary,"usage":usage,"created_at":created})
    except urllib.error.HTTPError as exc:
        _execute("DECLARE $user_id AS Utf8; DECLARE $day_key AS Utf8; DELETE FROM daily_summary_claims WHERE user_id=$user_id AND day_key=$day_key;", parameters={"$user_id":user_id,"$day_key":day_key})
        LOG.error("DeepSeek daily summary HTTP error status=%s", exc.code)
        return _json_response(502, {"error":"deepseek_provider_error","provider_status":int(exc.code)})
    except urllib.error.URLError as exc:
        _execute("DECLARE $user_id AS Utf8; DECLARE $day_key AS Utf8; DELETE FROM daily_summary_claims WHERE user_id=$user_id AND day_key=$day_key;", parameters={"$user_id":user_id,"$day_key":day_key})
        LOG.error("DeepSeek daily summary network error: %s", type(exc.reason).__name__)
        return _json_response(502, {"error":"deepseek_network_error"})
    except TableMissing:
        raise
    except Exception as exc:
        _execute("DECLARE $user_id AS Utf8; DECLARE $day_key AS Utf8; DELETE FROM daily_summary_claims WHERE user_id=$user_id AND day_key=$day_key;", parameters={"$user_id":user_id,"$day_key":day_key})
        safe_detail = str(exc).replace("\\n", " ")[:500]
        LOG.exception("Daily summary failed type=%s detail=%s", type(exc).__name__, safe_detail)
        return _json_response(502, {"error":"daily_summary_failed",
            "exception_type":type(exc).__name__, "detail":type(exc).__name__ + ": " + safe_detail})



def _deepseek_analyze_onboarding(user_id):
    from datetime import datetime, timezone
    import urllib.error
    state = _read_state(user_id)
    onboarding = state.get("onboarding") if isinstance(state, dict) else None
    if not isinstance(onboarding, dict):
        return _json_response(404, {"error":"onboarding_not_found"})
    cache_key = "onboarding-profile:v1"
    cached = _execute(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $entry_id AS Utf8;
        SELECT analysis_text, input_tokens, output_tokens, created_at
        FROM ai_analyses WHERE user_id = $user_id AND entry_id = $entry_id;
        """,
        parameters={"$user_id": user_id, "$entry_id": cache_key},
    )
    for result_set in cached:
        for row in result_set.rows:
            return _json_response(200, {"ok":True,"cached":True,"analysis":str(row.analysis_text)})
    prompt = (
        "Ты — персональный аналитик приложения «Путь к себе». На основе только ответов анкеты "
        "предложи не более трёх осторожных гипотез о возможных привычных убеждениях или реакциях. "
        "Не ставь диагнозов, не называй когнитивные искажения паттернами, не делай выводов о личности. "
        "Не выдавай гипотезы за факты. Формулируй каждую гипотезу от первого лица простыми словами, "
        "как мысль, которую человек может узнать или отвергнуть. Если данных мало — прямо скажи это. "
        "Верни краткое вступление, затем для каждой гипотезы отдельную строку строго формата "
        "'Паттерн анкеты: формулировка от первого лица | когда или почему эта мысль может возникать'. "
        "Не более 220 слов."
    )
    try:
        data = _deepseek_chat([
            {"role":"system","content":prompt},
            {"role":"user","content":"Ответы анкеты пользователя:\n"+json.dumps({
                "что_сейчас_беспокоит":str(onboarding.get("main_issue") or "")[:1500],
                "важные_сферы":onboarding.get("areas") or [],
                "знакомые_мысли":onboarding.get("thoughts") or [],
                "обычные_реакции":onboarding.get("reactions") or [],
                "чего_хочет_изменить":str(onboarding.get("desired_changes") or "")[:1500],
            },ensure_ascii=False)}
        ],max_tokens=1800,timeout=45)
        choices=data.get("choices") or []
        message=choices[0].get("message") if choices else {}
        analysis=str((message or {}).get("content") or "").strip()
        if not analysis:
            return _json_response(502,{"error":"empty_model_response"})
        usage=data.get("usage") or {}
        _save_ai_result(user_id,cache_key,analysis,
            {"input_tokens":int(usage.get("prompt_tokens",0)),"output_tokens":int(usage.get("completion_tokens",0))},
            datetime.now(timezone.utc).isoformat())
        return _json_response(200,{"ok":True,"cached":False,"analysis":analysis})
    except urllib.error.HTTPError as exc:
        LOG.error("Onboarding analysis HTTP error status=%s",exc.code)
        return _json_response(502,{"error":"deepseek_provider_error","provider_status":int(exc.code)})
    except TableMissing:
        raise
    except Exception as exc:
        LOG.exception("Onboarding analysis failed type=%s",type(exc).__name__)
        return _json_response(502,{"error":"onboarding_analysis_failed","exception_type":type(exc).__name__})


def _deepseek_connection_test():
    import urllib.error
    try:
        data = _deepseek_chat(
            [{"role": "user", "content": "Ты — Персональный аналитик приложения «Путь к себе». Ответь одной короткой фразой на русском: «Связь с DeepSeek установлена»."}],
            max_tokens=500,
            timeout=25,
        )
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices else {}
        response_text = str((message or {}).get("content") or "").strip()
        if not response_text:
            LOG.error("DeepSeek connection test returned no output")
            return _json_response(502, {"error": "empty_model_response"})
        return _json_response(200, {
            "ok": True, "model": DEEPSEEK_MODEL,
            "response": response_text, "usage": data.get("usage", {}),
        })
    except urllib.error.HTTPError as exc:
        try:
            provider_data = json.loads(exc.read(2000).decode("utf-8", errors="replace"))
            detail = str((provider_data.get("error") or {}).get("message", "DeepSeek request rejected"))[:500]
        except Exception:
            detail = "DeepSeek returned an unreadable error response"
        LOG.error("DeepSeek connection test HTTP error status=%s detail=%s", exc.code, detail)
        return _json_response(502, {"error": "deepseek_request_failed", "provider_status": int(exc.code), "detail": detail})
    except Exception as exc:
        LOG.error("DeepSeek connection test failed: %s", type(exc).__name__)
        return _json_response(502, {"error": "deepseek_request_failed", "detail": type(exc).__name__})


MINI_APP_URL = "https://oleggrigorev131313.github.io/put-k-sebe/"
DIARY_BUTTON_TEXT = "Открыть дневник"
# Stay under 25 messages/s. One send every 50ms is 20/s.
TELEGRAM_SEND_INTERVAL_SECONDS = 0.05
SUMMARY_NOTIFY_KEY_PREFIX = "notify:"
_last_telegram_send_at = 0.0
TELEGRAM_CONNECT_TIMEOUT_SECONDS = 5
TELEGRAM_TOTAL_TIMEOUT_SECONDS = 10
TELEGRAM_MAX_CONSECUTIVE_NETWORK_FAILURES = 3
_telegram_consecutive_network_failures = 0
# Blank lines below are real newline characters. Do not write the two characters \n.
SUMMARY_READY_TEXT = """\
✨ Твои итоги дня готовы!

Мы разобрали твои записи за сегодня: какие мысли повторялись, что удалось заметить и какие небольшие шаги могут помочь завтра.

👇 Нажми «Открыть дневник», чтобы посмотреть.\
"""
SUMMARY_EMPTY_TEXT = """\
🌙 Сегодня в дневнике не появилось новых записей, поэтому итоги дня подвести не получилось.

Перемены в мышлении складываются из маленьких шагов каждый день. Даже одна короткая запись или пять минут практики помогают лучше понять себя и заметить мысли, которые тянут назад.

Не пропускай дни, твой прогресс собирается из них 🌿

👇 Нажми «Открыть дневник» и добавь запись уже завтра или прямо сейчас.\
"""


def _diary_reply_markup():
    return {
        "inline_keyboard": [[{
            "text": DIARY_BUTTON_TEXT,
            "web_app": {"url": MINI_APP_URL},
        }]]
    }


def build_summary_notification(kind, chat_id):
    """Plain-text sendMessage body. The Mini App has no summary deep link."""
    if kind == "ready":
        text = SUMMARY_READY_TEXT
    elif kind == "empty":
        text = SUMMARY_EMPTY_TEXT
    else:
        raise ValueError("unknown summary notification")
    return {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": _diary_reply_markup(),
    }


def _summary_notification_kind(response):
    if not isinstance(response, dict):
        return None
    try:
        body = json.loads(response.get("body") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    status = response.get("statusCode")
    error = body.get("error")
    if status == 200 and body.get("summary"):
        return "ready"
    if status == 409 and error == "daily_summary_already_used":
        return "ready"
    if status == 400 and error == "no_entries_today":
        return "empty"
    if status == 404 and error == "user_state_not_found":
        return "empty"
    return None


def _pace_telegram_send():
    global _last_telegram_send_at
    gap = TELEGRAM_SEND_INTERVAL_SECONDS
    if gap <= 0:
        _last_telegram_send_at = time.monotonic()
        return
    wait = gap - (time.monotonic() - _last_telegram_send_at)
    if wait > 0:
        time.sleep(wait)
    _last_telegram_send_at = time.monotonic()


def _telegram_api_base():
    configured = os.environ.get("TELEGRAM_API_BASE", "").strip().rstrip("/")
    if not configured:
        return "https://api.telegram.org"
    return configured


def _telegram_urlopen(request):
    """POST with a 5s connect bound and a 10s overall socket timeout."""
    import http.client
    import socket
    import urllib.request

    class _TelegramHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            self.sock = socket.create_connection(
                (self.host, self.port),
                TELEGRAM_CONNECT_TIMEOUT_SECONDS,
            )
            self.sock.settimeout(TELEGRAM_TOTAL_TIMEOUT_SECONDS)
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)

    class _TelegramHTTPConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.create_connection(
                (self.host, self.port),
                TELEGRAM_CONNECT_TIMEOUT_SECONDS,
            )
            self.sock.settimeout(TELEGRAM_TOTAL_TIMEOUT_SECONDS)

    class _TelegramHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_TelegramHTTPSConnection, req)

    class _TelegramHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_TelegramHTTPConnection, req)

    opener = urllib.request.build_opener(_TelegramHTTPSHandler, _TelegramHTTPHandler)
    return opener.open(request, timeout=TELEGRAM_TOTAL_TIMEOUT_SECONDS)


def _send_telegram_message(method, payload):
    """Call the Bot API. Never log the URL: it contains the bot token."""
    import urllib.error
    import urllib.request
    if not BOT_TOKEN:
        LOG.error("Telegram %s skipped: BOT_TOKEN is not configured", method)
        return "skipped"
    url = _telegram_api_base() + "/bot" + BOT_TOKEN + "/" + method
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _telegram_urlopen(request) as response:
            raw = response.read(16_000)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            LOG.error("Telegram %s returned unreadable JSON", method)
            return "error"
        if not parsed.get("ok"):
            code = parsed.get("error_code")
            LOG.error("Telegram %s rejected error_code=%s", method, code)
            if code == 403:
                return "blocked"
            return "error"
        return "ok"
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", None)
        LOG.error("Telegram %s failed status=%s", method, code if code is not None else "unknown")
        if code == 403:
            return "blocked"
        return "error"
    except (urllib.error.URLError, TimeoutError, OSError):
        LOG.error("Telegram %s network failure", method)
        return "network"
    except Exception as exc:
        LOG.error("Telegram %s failed type=%s", method, type(exc).__name__)
        return "error"


def _claim_summary_notification(user_id, day_key, created_at):
    # Same table as the summary lock. The notify: prefix does not collide with YYYY-MM-DD.
    claim_key = SUMMARY_NOTIFY_KEY_PREFIX + day_key
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8; DECLARE $day_key AS Utf8; DECLARE $created_at AS Utf8;
            INSERT INTO daily_summary_claims (user_id, day_key, created_at)
            VALUES ($user_id, $day_key, $created_at);
            """,
            parameters={
                "$user_id": user_id,
                "$day_key": claim_key,
                "$created_at": created_at,
            },
            table="daily_summary_claims",
        )
        return True
    except TableMissing:
        raise
    except Exception:
        return False


def _release_summary_notification(user_id, day_key):
    claim_key = SUMMARY_NOTIFY_KEY_PREFIX + day_key
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8; DECLARE $day_key AS Utf8;
            DELETE FROM daily_summary_claims WHERE user_id=$user_id AND day_key=$day_key;
            """,
            parameters={"$user_id": user_id, "$day_key": claim_key},
            table="daily_summary_claims",
        )
    except TableMissing:
        raise
    except Exception as exc:
        LOG.error("summary notify release failed type=%s", type(exc).__name__)


def _send_summary_notification(user_id, day_key, response, created_at):
    global _telegram_consecutive_network_failures
    try:
        kind = _summary_notification_kind(response)
        if kind is None:
            return None
        if not str(user_id).isdigit():
            LOG.error("summary notify skipped: chat id is not numeric")
            return None
        if not BOT_TOKEN:
            LOG.error("summary notify skipped: BOT_TOKEN is not configured")
            return None
        if _telegram_consecutive_network_failures >= TELEGRAM_MAX_CONSECUTIVE_NETWORK_FAILURES:
            LOG.error("summary notify stopped after network failures")
            return "stopped"
        if not _claim_summary_notification(user_id, day_key, created_at):
            return "duplicate"
        _pace_telegram_send()
        result = _send_telegram_message(
            "sendMessage",
            build_summary_notification(kind, int(user_id)),
        )
        if result == "network":
            _telegram_consecutive_network_failures += 1
        else:
            _telegram_consecutive_network_failures = 0
        if result not in ("ok", "blocked"):
            _release_summary_notification(user_id, day_key)
        LOG.info("summary notify kind=%s result=%s", kind, result)
        return result
    except TableMissing:
        raise
    except Exception as exc:
        LOG.error("summary notify failed type=%s", type(exc).__name__)
        return "error"


def _local_day_key(timezone_name, now):
    return now.astimezone(_safe_user_timezone(timezone_name)).date().isoformat()


def build_inactivity_reminder(chat_id, variant):
    index = int(variant)
    if index < 0 or index >= len(INACTIVITY_REMINDER_TEXTS):
        raise ValueError("invalid reminder variant")
    return {
        "chat_id": int(chat_id),
        "text": INACTIVITY_REMINDER_TEXTS[index],
        "reply_markup": _diary_reply_markup(),
    }


def _load_reminder_rows():
    rows = {}
    result = _execute(
        """
        SELECT user_id, reminder_sent_at, last_reminder_variant, telegram_blocked
        FROM user_reminders;
        """,
        table="user_reminders",
    )
    for result_set in result:
        for row in result_set.rows:
            uid = str(row.user_id)
            rows[uid] = {
                "reminder_sent_at": str(row.reminder_sent_at or ""),
                "last_reminder_variant": int(row.last_reminder_variant)
                if row.last_reminder_variant is not None
                else -1,
                "telegram_blocked": bool(row.telegram_blocked),
            }
    return rows


def _upsert_reminder_row(user_id, reminder_sent_at, variant, telegram_blocked):
    _execute(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $reminder_sent_at AS Utf8;
        DECLARE $last_reminder_variant AS Int32;
        DECLARE $telegram_blocked AS Bool;
        UPSERT INTO user_reminders (
            user_id, reminder_sent_at, last_reminder_variant, telegram_blocked
        )
        VALUES ($user_id, $reminder_sent_at, $last_reminder_variant, $telegram_blocked);
        """,
        parameters={
            "$user_id": str(user_id),
            "$reminder_sent_at": str(reminder_sent_at or "")[:40],
            "$last_reminder_variant": int(variant),
            "$telegram_blocked": bool(telegram_blocked),
        },
        table="user_reminders",
    )


def _run_inactivity_reminders(now=None):
    from datetime import datetime, timezone

    global _telegram_consecutive_network_failures
    _telegram_consecutive_network_failures = 0
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        profiles = _execute(
            "SELECT user_id, timezone, last_seen FROM user_profiles;",
            table="user_profiles",
        )
        reminders = _load_reminder_rows()
    except TableMissing as exc:
        LOG.error("Inactivity reminder skipped stage=table_missing:%s", exc.table_name)
        print("PUT_K_SEBE_503 stage=table_missing:%s" % exc.table_name, flush=True)
        return
    for result_set in profiles:
        for row in result_set.rows:
            user_id = str(row.user_id)
            last_seen = str(row.last_seen or "")
            meta = reminders.get(user_id, {
                "reminder_sent_at": "",
                "last_reminder_variant": -1,
                "telegram_blocked": False,
            })
            if not should_send_inactivity_reminder(
                user_id,
                last_seen,
                meta,
                now,
                ai_processing=_get_user_consents(user_id).get("ai_processing"),
            ):
                continue
            if _telegram_consecutive_network_failures >= TELEGRAM_MAX_CONSECUTIVE_NETWORK_FAILURES:
                LOG.error("Inactivity reminder stopped after network failures")
                return
            variant = pick_reminder_variant(meta.get("last_reminder_variant", -1))
            if not _claim_inactivity_reminder(user_id, last_seen, now.isoformat()):
                continue
            _pace_telegram_send()
            result = _send_telegram_message(
                "sendMessage",
                build_inactivity_reminder(int(user_id), variant),
            )
            if result == "ok":
                _upsert_reminder_row(user_id, now.isoformat(), variant, False)
            elif result == "blocked":
                _upsert_reminder_row(user_id, now.isoformat(), variant, True)
            elif result == "network":
                _telegram_consecutive_network_failures += 1
                _release_inactivity_reminder(user_id, last_seen)
            else:
                _release_inactivity_reminder(user_id, last_seen)
            LOG.info("inactivity reminder user=%s result=%s", user_id, result)


def _inactivity_claim_key(last_seen):
    return "inactivity:" + hashlib.sha256(last_seen.encode("utf-8")).hexdigest()[:24]


def _claim_inactivity_reminder(user_id, last_seen, created_at):
    claim_key = _inactivity_claim_key(last_seen)
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8;
            DECLARE $day_key AS Utf8;
            DECLARE $created_at AS Utf8;
            INSERT INTO daily_summary_claims (user_id, day_key, created_at)
            VALUES ($user_id, $day_key, $created_at);
            """,
            parameters={
                "$user_id": user_id,
                "$day_key": claim_key,
                "$created_at": created_at,
            },
            table="daily_summary_claims",
        )
        return True
    except TableMissing:
        raise
    except Exception:
        return False


def _release_inactivity_reminder(user_id, last_seen):
    claim_key = _inactivity_claim_key(last_seen)
    try:
        _execute(
            """
            DECLARE $user_id AS Utf8;
            DECLARE $day_key AS Utf8;
            DELETE FROM daily_summary_claims WHERE user_id=$user_id AND day_key=$day_key;
            """,
            parameters={"$user_id": user_id, "$day_key": claim_key},
            table="daily_summary_claims",
        )
    except TableMissing:
        raise
    except Exception as exc:
        LOG.error("inactivity reminder release failed type=%s", type(exc).__name__)


def _run_due_daily_summaries(now=None):
    from datetime import datetime, timezone
    global _telegram_consecutive_network_failures
    if not DAILY_SUMMARY_TELEGRAM_PUSH_ENABLED:
        LOG.info("Scheduled daily summary push disabled (DAILY_SUMMARY_TELEGRAM_PUSH_ENABLED)")
        return
    _telegram_consecutive_network_failures = 0
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        listed = _execute(
            "SELECT timezone FROM user_profiles GROUP BY timezone;",
            table="user_profiles",
        )
    except TableMissing:
        LOG.error("Scheduled summary skipped stage=table_missing:user_profiles")
        print("PUT_K_SEBE_503 stage=table_missing:user_profiles", flush=True)
        return
    due = []
    seen = set()
    for result_set in listed:
        for row in result_set.rows:
            tz_name = str(row.timezone or "UTC")
            if tz_name in seen:
                continue
            seen.add(tz_name)
            if now.astimezone(_safe_user_timezone(tz_name)).strftime("%H:%M") == "23:59":
                due.append(tz_name)
    if not due:
        return
    for tz_name in due:
        if tz_name == "UTC":
            query = """
                DECLARE $timezone AS Utf8;
                SELECT user_id FROM user_profiles
                WHERE timezone = $timezone OR timezone IS NULL;
            """
        else:
            query = """
                DECLARE $timezone AS Utf8;
                SELECT user_id FROM user_profiles WHERE timezone = $timezone;
            """
        rows = _execute(query, parameters={"$timezone": tz_name}, table="user_profiles")
        for result_set in rows:
            for row in result_set.rows:
                user_id = str(row.user_id)
                try:
                    if not _get_user_consents(user_id).get("ai_processing"):
                        continue
                    response = _deepseek_daily_summary(user_id, tz_name, False)
                    _send_summary_notification(
                        user_id,
                        _local_day_key(tz_name, now),
                        response,
                        now.isoformat(),
                    )
                except TableMissing as exc:
                    LOG.error("Scheduled summary skipped stage=table_missing:%s", exc.table_name)
                    print("PUT_K_SEBE_503 stage=table_missing:%s" % exc.table_name, flush=True)
                    return
                except Exception:
                    LOG.exception("Scheduled summary failed")
    

MINI_APP_URL = "https://oleggrigorev131313.github.io/put-k-sebe/"
WELCOME_PHOTO_URL = MINI_APP_URL + "assets/bot/welcome.jpg"
DIARY_BUTTON_TEXT = "Открыть дневник"
TELEGRAM_CAPTION_LIMIT = 1024
# Domain separation for the webhook secret derived from BOT_TOKEN. Not a credential.
# If TELEGRAM_WEBHOOK_SECRET is set, that value is used instead of the derivation.
TELEGRAM_WEBHOOK_SECRET_LABEL = b"put-k-sebe-telegram-webhook-v1"
WELCOME_CAPTION = """\
Привет! 👋 На связи команда ПЕРЕСМОТР.

У каждого из нас есть мысли, которые повторяются снова и снова: «я недостаточно хорош», «у меня всегда всё идёт не так», «я должен быть лучше всех». Часто мы их даже не замечаем, а они годами влияют на настроение, решения и отношения.

ПЕРЕСМОТР помогает:
🔍 увидеть свои повторяющиеся паттерны мышления
✍️ разобрать, где мысль искажает реальность
🔄 переписать её на более честный и добрый взгляд на себя
📊 следить, как со временем меняется твоё состояние

15 бесплатных подсказок персонального ассистента — без активации. Записи в дневнике остаются бесплатными и без лимита.

Шаг за шагом ты отпускаешь убеждения, которые причиняют боль, и становишься той версией себя, которой хочешь быть.

👇 Нажми кнопку «Открыть дневник» под этим сообщением, и он откроется прямо в Telegram. Ничего скачивать не нужно.\
"""


def _telegram_text_units(text):
    # Bot API length limits are UTF-16 code units, which is what Telegram stores.
    return len(text.encode("utf-16-le")) // 2


def _is_start_command(text):
    if not isinstance(text, str):
        return False
    head = text.strip().split(None, 1)[0] if text.strip() else ""
    command = head.split("@", 1)[0]
    return command.casefold() == "/start"


def _expected_webhook_secret():
    configured = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if configured:
        return configured
    if not BOT_TOKEN:
        return ""
    return hmac.new(
        BOT_TOKEN.encode("utf-8"),
        TELEGRAM_WEBHOOK_SECRET_LABEL,
        hashlib.sha256,
    ).hexdigest()


def _telegram_webhook_authorized(headers):
    expected = _expected_webhook_secret()
    provided = str(headers.get("x-telegram-bot-api-secret-token", "")).strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def _diary_reply_markup():
    return {
        "inline_keyboard": [[{
            "text": DIARY_BUTTON_TEXT,
            "web_app": {"url": MINI_APP_URL},
        }]]
    }


def build_start_reply(chat_id, caption=None):
    """Telegram methods for /start. No parse_mode: the caption is plain text."""
    text = WELCOME_CAPTION if caption is None else caption
    button = _diary_reply_markup()
    photo = {"chat_id": chat_id, "photo": WELCOME_PHOTO_URL}
    if _telegram_text_units(text) <= TELEGRAM_CAPTION_LIMIT:
        photo["caption"] = text
        photo["reply_markup"] = button
        return [("sendPhoto", photo)]
    return [
        ("sendPhoto", photo),
        ("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": button}),
    ]


def _webhook_response(payload):
    # API Gateway payload format 1.0 copies statusCode, headers, and body
    # into the HTTP response. isBase64Encoded stays false so the JSON is raw.
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "isBase64Encoded": False,
    }


def _start_webhook_method(chat_id):
    """One Bot API method for Telegram's webhook-response feature. No outbound call."""
    method, fields = build_start_reply(chat_id)[0]
    reply = {"method": method}
    reply.update(fields)
    return reply


def _handle_telegram_webhook(headers, body):
    # Bot updates only. No YDB reads or writes, and no call to api.telegram.org.
    if not _telegram_webhook_authorized(headers):
        LOG.error("Telegram webhook rejected")
        return _json_response(401, {"error": "unauthorized"})
    try:
        if not isinstance(body, str):
            body = ""
        if len(body.encode("utf-8")) > 100_000:
            LOG.error("Telegram webhook body too large")
            return _webhook_response({"ok": True})
        try:
            update = json.loads(body or "{}")
        except json.JSONDecodeError:
            LOG.error("Telegram webhook ignored: invalid JSON")
            return _webhook_response({"ok": True})
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict) or not _is_start_command(message.get("text")):
            return _webhook_response({"ok": True})
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        # web_app buttons are delivered in a private chat with the bot.
        if chat.get("type") not in (None, "private"):
            return _webhook_response({"ok": True})
        chat_id = chat.get("id")
        if isinstance(chat_id, bool) or not isinstance(chat_id, int):
            LOG.error("Telegram /start ignored: chat id missing")
            return _webhook_response({"ok": True})
        from_user = message.get("from") if isinstance(message.get("from"), dict) else {}
        uid = from_user.get("id")
        text = message.get("text") if isinstance(message.get("text"), str) else ""
        parts = text.strip().split(None, 1)
        payload = parts[1] if len(parts) > 1 else ""
        if isinstance(uid, int) and payload:
            try:
                _imeads_capture_click_id(str(uid), payload)
            except Exception:
                pass
            try:
                _source_capture_first_touch(str(uid), payload)
            except Exception:
                pass
            try:
                src_tag = parse_source_tag(payload)
                if src_tag:
                    _source_increment_start(src_tag)
            except Exception:
                pass
        LOG.info("Telegram /start inline reply")
        return _webhook_response(_start_webhook_method(chat_id))
    except Exception as exc:
        LOG.error("Telegram webhook failed type=%s", type(exc).__name__)
        return _webhook_response({"ok": True})


def handler(event, context):
    try:
        messages=event.get("messages") if isinstance(event,dict) else None
        if messages:
            for msg in messages:
                try: payload=json.loads((msg.get("details") or {}).get("payload") or "{}")
                except (TypeError,json.JSONDecodeError): payload={}
                job = payload.get("job")
                if job == "daily_summary":
                    _run_due_daily_summaries()
                elif job == "inactivity_reminder":
                    _run_inactivity_reminders()
            return {"statusCode":200,"body":"ok"}
        method, path, headers, body, query = _request_parts(event)

        if method == "OPTIONS":
            return _preflight_response()

        if path == "/health" and method == "GET":
            # Liveness only. Table readiness is /health/schema and does not create tables.
            try:
                _execute("SELECT 1 AS health_check;")
                return _json_response(200, {
                    "ok": True, "service": "put-k-sebe-api",
                    "checks": {"handler": "ok", "ydb": "ok"}
                })
            except Exception as exc:
                LOG.exception("Health check failed stage=ydb_connect_or_query")
                return _json_response(503, {
                    "ok": False, "stage": "ydb_connect_or_query",
                    "exception_type": type(exc).__name__,
                    "detail": str(exc)[:700]
                })

        if path == "/health/db" and method == "GET":
            try:
                _execute("SELECT 1 AS health_check;")
                return _json_response(200, {
                    "ok": True, "checks": {"handler": "ok", "ydb": "ok"}
                })
            except Exception as exc:
                LOG.exception("Health check failed stage=ydb_connect_or_query")
                return _json_response(503, {
                    "ok": False, "stage": "ydb_connect_or_query",
                    "exception_type": type(exc).__name__,
                    "detail": str(exc)[:700]
                })

        if path == "/health/schema" and method == "GET":
            tables = {}
            try:
                for name in TABLES:
                    try:
                        _probe_table(name)
                        tables[name] = "ok"
                    except TableMissing:
                        tables[name] = "missing"
                missing = [name for name, status in tables.items() if status != "ok"]
                if missing:
                    stage = "table_missing:" + missing[0]
                    LOG.error("Schema readiness missing tables=%s", ",".join(missing))
                    print("PUT_K_SEBE_503 stage=%s" % stage, flush=True)
                    return _json_response(503, {
                        "ok": False,
                        "stage": stage,
                        "missing": missing,
                        "checks": {
                            "tables": tables,
                            "user_consents": tables.get("user_consents", "missing"),
                            "user_state": tables.get("user_state", "missing"),
                        },
                    })
                return _json_response(200, {
                    "ok": True,
                    "checks": {
                        "handler": "ok",
                        "tables": tables,
                        "user_consents": "ok",
                        "user_state": "ok",
                    },
                })
            except Exception as exc:
                LOG.exception("Health check failed stage=schema_probe")
                return _json_response(503, {
                    "ok": False,
                    "stage": "schema_probe",
                    "exception_type": type(exc).__name__,
                    "detail": str(exc)[:700],
                    "checks": {"tables": tables},
                })

        if path == "/v1/webhooks/telegram" and method == "POST":
            return _handle_telegram_webhook(headers, body)

        if method == "GET" and path.startswith("/go/"):
            tag = path[4:].lstrip("/").split("/", 1)[0]
            return _go_source_redirect(tag)

        if path == "/v1/webhooks/tribute" and method == "POST":
            return _handle_tribute_webhook(
                body.encode("utf-8"), headers.get("trbt-signature", "")
            )

        if path == "/v1/consents" and method in ("GET", "PUT"):
            user_id = _auth_telegram_user(headers)
            if method == "GET":
                return _json_response(200, {"ok": True, "consents": _get_user_consents(user_id)})
            try:
                payload = json.loads(body or "{}")
                processing = payload.get("ai_processing")
                history = payload.get("ai_history_storage")
                if not isinstance(processing, bool) or not isinstance(history, bool) or (history and not processing):
                    raise ValueError("invalid_consent_values")
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                return _json_response(400, {"error": str(exc)})
            previous = _get_user_consents(user_id)
            saved = _save_user_consents(user_id, processing, history)
            if previous["ai_history_storage"] and not history:
                _delete_ai_history(user_id)
            if processing:
                try:
                    _imeads_check_signup(user_id)
                except Exception:
                    pass
            return _json_response(200, {"ok": True, "consents": saved})
        if path == "/v1/profile" and method == "POST":
            user_id = _auth_telegram_user(headers)
            try:
                payload = json.loads(body or "{}")
                _upsert_profile(user_id,payload.get("user") or {},str(payload.get("timezone") or "UTC"))
                start_param = payload.get("start_param") or payload.get("startParam")
                if start_param:
                    try:
                        _imeads_capture_click_id(user_id, start_param)
                    except Exception:
                        pass
                    try:
                        _source_capture_first_touch(user_id, start_param)
                    except Exception:
                        pass
                return _json_response(200,{"ok":True})
            except (json.JSONDecodeError,TypeError,ValueError):
                return _json_response(400,{"error":"invalid_profile"})

        if path == "/v1/premium" and method == "GET":
            user_id = _auth_telegram_user(headers)
            return _json_response(200, _premium_status(user_id))

        if path == "/v1/trial" and method == "POST":
            return _json_response(410, {"error": "trial_removed", "message": "Пробный период больше не используется."})
        if path in ("/v1/assistant/hint", "/assistant/hint") and method == "POST":
            user_id = _auth_telegram_user(headers)
            try:
                payload = json.loads(body or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("invalid_body")
            except (json.JSONDecodeError, ValueError, TypeError):
                return _json_response(400, {"error": "invalid_body"})
            timezone_name = _resolve_timezone(headers, query, payload)
            return _deepseek_assistant_hint(user_id, payload, timezone_name)
        if path == "/v1/ai/test" and method == "POST":
            user_id = _auth_telegram_user(headers)
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error": "deepseek_not_configured"})
            return _deepseek_connection_test()
        if path == "/v1/ai/analyze-onboarding" and method == "POST":
            user_id = _auth_telegram_user(headers)
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error":"deepseek_not_configured"})
            denied = _require_ai_processing(user_id)
            if denied:
                return denied
            return _deepseek_analyze_onboarding(user_id)

        if path == "/v1/ai/daily-summary" and method == "POST":
            user_id = _auth_telegram_user(headers)
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error":"deepseek_not_configured"})
            denied = _require_ai_processing(user_id)
            if denied:
                return denied
            try:
                payload = json.loads(body or "{}")
                timezone_name = _resolve_timezone(headers, query, payload)
                action = str(payload.get("action") or "")
            except (json.JSONDecodeError, TypeError):
                timezone_name = _resolve_timezone(headers, query, None)
                action = ""
            if action == "previous":
                return _get_previous_daily_summary(user_id, timezone_name)
            return _deepseek_daily_summary(user_id, timezone_name, False)

        if path == "/v1/ai/usage" and method == "GET":
            user_id = _auth_telegram_user(headers)
            timezone_name = _resolve_timezone(headers, query, None)
            status = _assistant_quota_status(user_id, timezone_name)
            return _json_response(200, status)

        if path == "/v1/ai/analyze-entry" and method == "POST":
            user_id = _auth_telegram_user(headers)
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error": "deepseek_not_configured"})
            denied = _require_ai_processing(user_id)
            if denied:
                return denied
            try:
                payload = json.loads(body or "{}")
                entry_id = str(payload.get("entry_id", "")).strip()
                if not entry_id or len(entry_id) > 128:
                    raise ValueError("invalid_entry_id")
            except (json.JSONDecodeError, ValueError, TypeError):
                return _json_response(400, {"error": "invalid_request"})
            timezone_name = _resolve_timezone(headers, query, payload)
            return _deepseek_analyze_entry(user_id, entry_id, timezone_name)

        if path == "/v1/admin/source-tags" and method == "GET":
            if not ADMIN_PASSWORD or not hmac.compare_digest(
                headers.get("x-admin-password", ""), ADMIN_PASSWORD
            ):
                return _json_response(401, {"error": "unauthorized"})
            try:
                tags = _source_list_tags_with_stats()
            except TableMissing as exc:
                return _json_response(
                    503, {"error": "table_missing", "table": exc.table_name}
                )
            return _json_response(200, {"ok": True, "tags": tags})

        if path == "/v1/admin/source-tags" and method == "POST":
            if not ADMIN_PASSWORD or not hmac.compare_digest(
                headers.get("x-admin-password", ""), ADMIN_PASSWORD
            ):
                return _json_response(401, {"error": "unauthorized"})
            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                return _json_response(400, {"error": "invalid_json"})
            try:
                created = _source_create_tag(payload)
            except TableMissing as exc:
                return _json_response(
                    503, {"error": "table_missing", "table": exc.table_name}
                )
            except ValueError as exc:
                return _json_response(
                    400,
                    {"error": str(exc), "message": admin_source_error(str(exc))},
                )
            gateway_base = os.environ.get("PUBLIC_GATEWAY_URL", "").strip().rstrip("/")
            track_url = (
                f"{gateway_base}/go/{created['tag']}" if gateway_base else ""
            )
            return _json_response(
                200,
                {
                    "ok": True,
                    "tag": created,
                    "track_url": track_url,
                    "telegram_url": created["telegram_url"],
                },
            )

        if path.startswith("/v1/admin/source-tags/") and method == "DELETE":
            if not ADMIN_PASSWORD or not hmac.compare_digest(
                headers.get("x-admin-password", ""), ADMIN_PASSWORD
            ):
                return _json_response(401, {"error": "unauthorized"})
            tag = path[len("/v1/admin/source-tags/") :].strip("/")
            if tag:
                tag = unquote(tag)
            try:
                _source_delete_tag(tag)
            except TableMissing as exc:
                return _json_response(
                    503, {"error": "table_missing", "table": exc.table_name}
                )
            except ValueError as exc:
                return _json_response(
                    400,
                    {"error": str(exc), "message": admin_source_error(str(exc))},
                )
            return _json_response(200, {"ok": True, "deleted": True, "tag": tag})

        if path == "/v1/admin/imeads/test" and method == "POST":
            if not ADMIN_PASSWORD or not hmac.compare_digest(headers.get("x-admin-password", ""), ADMIN_PASSWORD):
                return _json_response(401, {"error": "unauthorized"})
            if not IMEADS_SECRET_KEY:
                return _json_response(503, {"error": "imeads_not_configured"})
            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                payload = {}
            click_id = _parse_imeads_click_id(payload.get("click_id") or payload.get("start_param"))
            target_id = str(payload.get("user_id") or "").strip()
            if not click_id and target_id.isdigit():
                try:
                    row = _imeads_read(target_id)
                    if row:
                        click_id = row["click_id"]
                except TableMissing:
                    return _json_response(503, {"error": "imeads_table_missing"})
            if not click_id:
                return _json_response(400, {"error": "click_id_invalid_or_missing"})
            result = _imeads_fire_postback(click_id, "test", log_errors=False)
            return _json_response(
                200 if result.get("ok") else 502,
                {
                    "ok": bool(result.get("ok")),
                    "sent": "test",
                    "imeads_status": result.get("status"),
                    "imeads_body": result.get("body") or "",
                },
            )

        if path == "/v1/journal/funnel" and method == "POST":
            user_id = _auth_telegram_user(headers)
            try:
                payload = json.loads(body or "{}")
                event_kind = str(payload.get("kind") or payload.get("event_kind") or "").strip()
                if event_kind not in ("step_opened", "entry_completed"):
                    raise ValueError("invalid_kind")
                step_raw = payload.get("step", payload.get("step_number", -1))
                step_number = int(step_raw)
                if event_kind == "step_opened":
                    if step_number < 0 or step_number > 5:
                        raise ValueError("invalid_step")
                else:
                    step_number = -1
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                return _json_response(400, {"error": str(exc)})
            from datetime import datetime, timezone

            try:
                _record_journal_funnel_event(
                    user_id,
                    event_kind,
                    step_number,
                    datetime.now(timezone.utc).isoformat(),
                )
                return _json_response(202, {"ok": True})
            except TableMissing:
                raise
            except Exception as exc:
                LOG.error("journal funnel event failed type=%s", type(exc).__name__)
                return _json_response(503, {"error": "journal_funnel_unavailable"})

        if path == "/v1/events" and method == "POST":
            user_id = _auth_telegram_user(headers)
            try:
                payload = json.loads(body or "{}")
                event_name = payload.get("event")
                allowed = {
                    "app_open", "onboarding_started", "onboarding_completed",
                    "diary_entry_created", "practice_opened", "practice_completed",
                    "pattern_created", "pattern_updated", "pattern_deleted",
                    "personal_practice_added", "personal_practice_completed",
                    "daily_summary_created", "ai_analysis_created",
                    "journal_step_opened", "journal_entry_completed",
                }
                if event_name not in allowed: raise ValueError("Invalid event")
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"),dict) else {}
                _record_event(user_id,event_name,metadata)
                return _json_response(202, {"ok": True})
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                return _json_response(400, {"error": str(exc)})

        if path == "/v1/admin/users" and method == "GET":
            if not ADMIN_PASSWORD or not hmac.compare_digest(headers.get("x-admin-password", ""), ADMIN_PASSWORD):
                return _json_response(401, {"error": "unauthorized"})
            result = _execute("SELECT user_id, username, first_name, last_name, timezone, created_at, last_seen FROM user_profiles ORDER BY last_seen DESC LIMIT 2000;")
            users = []
            for rs in result:
                for row in rs.rows:
                    uid=str(row.user_id); state=_read_state(uid) or {}
                    try: premium=_premium_status(uid)
                    except TableMissing: raise
                    except Exception: premium={"active":False,"source":None,"trial_used":False}
                    users.append({"telegram_id":uid,"username":str(row.username or ""),
                        "first_name":str(row.first_name or ""),"last_name":str(row.last_name or ""),
                        "timezone":str(row.timezone or "UTC"),"created_at":str(row.created_at),
                        "last_seen":str(row.last_seen),"entries":len(state.get("entries") or []),
                        "practices_completed":len(state.get("practiceHistory") or []),
                        "patterns":len(state.get("patterns") or []),
                        "personal_practices":len(state.get("personalPractices") or []),"premium":premium})
            return _json_response(200,{"ok":True,"users":users})

        if path == "/v1/admin/user" and method == "GET":
            if not ADMIN_PASSWORD or not hmac.compare_digest(headers.get("x-admin-password", ""), ADMIN_PASSWORD):
                return _json_response(401, {"error": "unauthorized"})
            query=event.get("queryStringParameters") or {}
            target_id=str(query.get("user_id") or "").strip()
            if not target_id.isdigit(): return _json_response(400,{"error":"invalid_user_id"})
            state=_read_state(target_id) or {}
            analytics_id=hmac.new(ANALYTICS_SECRET.encode("utf-8"),("put-k-sebe-analytics:"+target_id).encode("utf-8"),hashlib.sha256).hexdigest()
            result=_execute("""
                DECLARE $user_id AS Utf8;
                SELECT event_name,created_at,metadata_json FROM analytics_events
                WHERE user_id=$user_id ORDER BY created_at DESC LIMIT 500;
            """,parameters={"$user_id":analytics_id})
            events=[]
            for rs in result:
                for row in rs.rows:
                    events.append({"event":str(row.event_name),"created_at":row.created_at.isoformat() if hasattr(row.created_at,"isoformat") else str(row.created_at),"metadata":json.loads(str(row.metadata_json or "{}"))})
            consents=_get_user_consents(target_id)
            ai_history=[]
            if consents.get("ai_history_storage"):
                history_result=_execute("""
                    DECLARE $user_id AS Utf8;
                    SELECT entry_id,analysis_text,created_at,input_tokens,output_tokens
                    FROM ai_analyses WHERE user_id=$user_id ORDER BY created_at DESC LIMIT 300;
                """,parameters={"$user_id":target_id})
                for rs in history_result:
                    for row in rs.rows:
                        ai_history.append({"entry_id":str(row.entry_id),"text":str(row.analysis_text),
                            "created_at":str(row.created_at),"input_tokens":int(row.input_tokens),
                            "output_tokens":int(row.output_tokens)})
            else:
                state=dict(state); state["entries"]=[dict(x,analysis=None) for x in (state.get("entries") or [])]; state["chat"]=[]
            return _json_response(200,{"ok":True,"telegram_id":target_id,"state":state,"consents":consents,
                "events":events,"ai_history":ai_history,"premium":_premium_status(target_id)})

        if path == "/v1/admin/journal-funnel" and method == "GET":
            if not ADMIN_PASSWORD or not hmac.compare_digest(
                headers.get("x-admin-password", ""), ADMIN_PASSWORD
            ):
                return _json_response(401, {"error": "unauthorized"})
            period_raw = str((query or {}).get("days") or "30").strip()
            try:
                period_days = int(period_raw)
            except ValueError:
                period_days = 30
            if period_days not in (0, 7, 30):
                period_days = 30
            try:
                result = _execute(
                    """
                    SELECT user_id, event_kind, step_number, created_at
                    FROM journal_funnel_events
                    ORDER BY created_at DESC
                    LIMIT 200000;
                    """,
                    table="journal_funnel_events",
                )
            except TableMissing:
                return _json_response(
                    503,
                    {"error": "table_missing", "message": "journal_funnel_events"},
                )
            rows = []
            for result_set in result:
                for row in result_set.rows:
                    rows.append(
                        {
                            "user_id": str(row.user_id),
                            "event_kind": str(row.event_kind),
                            "step_number": int(row.step_number)
                            if row.step_number is not None
                            else -1,
                            "created_at": str(row.created_at),
                        }
                    )
            stats = build_funnel_stats(
                rows,
                days=None if period_days == 0 else period_days,
            )
            return _json_response(200, {"ok": True, **stats})

        if path == "/v1/admin/analytics" and method == "GET":
            if not ADMIN_PASSWORD or not hmac.compare_digest(
                headers.get("x-admin-password", ""), ADMIN_PASSWORD
            ):
                return _json_response(401, {"error": "unauthorized"})
            result = _execute(
                """
                SELECT user_id, event_name, created_at
                FROM analytics_events
                ORDER BY created_at DESC
                LIMIT 50000;
                """
            )
            rows = []
            for result_set in result:
                for row in result_set.rows:
                    rows.append({
                        "user_id": str(row.user_id),
                        "event": str(row.event_name),
                        "created_at": row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else str(row.created_at),
                    })
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            def parsed(value):
                try:
                    parsed_value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    # YDB Timestamp values or legacy ISO strings may arrive without tzinfo.
                    # Treat naive timestamps as UTC so comparisons with aware UTC "now" are safe.
                    if parsed_value.tzinfo is None:
                        return parsed_value.replace(tzinfo=timezone.utc)
                    return parsed_value.astimezone(timezone.utc)
                except (TypeError, ValueError, AttributeError):
                    return now
            counts = {}
            daily = {}
            unique_all, dau, wau, mau = set(), set(), set(), set()
            for row in rows:
                uid = row["user_id"]
                dt = parsed(row["created_at"])
                unique_all.add(uid)
                counts[row["event"]] = counts.get(row["event"], 0) + 1
                if row["event"] == "app_open":
                    day = dt.date().isoformat()
                    daily[day] = daily.get(day, 0) + 1
                    if dt.date() == now.date(): dau.add(uid)
                    if dt >= now - timedelta(days=7): wau.add(uid)
                    if dt >= now - timedelta(days=30): mau.add(uid)
            return _json_response(200, {
                "ok": True, "users": len(unique_all), "dau": len(dau),
                "wau": len(wau), "mau": len(mau), "events": counts,
                "daily_opens": [{"date": k, "opens": v} for k, v in sorted(daily.items())[-30:]],
                "total_events_loaded": len(rows), "truncated": len(rows) >= 50000
            })

        if path != "/v1/state":
            return _json_response(404, {"error": "not_found"})

        if method not in ("GET", "PUT", "DELETE"):
            return _json_response(405, {"error": "method_not_allowed"})

        user_id = _auth_telegram_user(headers)

        if method == "GET":
            state = _read_state(user_id)
            # No persistent usage event is recorded until the user has submitted consented state.
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

            previous = _read_state(user_id)
            _write_state(user_id, state)

            if previous is None:
                _record_event(user_id, "user_registered")
            if previous is None or not previous.get("onboarding"):
                if state.get("onboarding") and not state["onboarding"].get("skipped"):
                    _record_event(user_id, "onboarding_completed")
            try:
                _imeads_check_signup(user_id)
            except Exception:
                pass

            return _json_response(200, {"ok": True, "saved": True})

        if method == "DELETE":
            _execute(
                """
                DECLARE $user_id AS Utf8;
                DELETE FROM user_state WHERE user_id = $user_id;
                """,
                parameters={"$user_id": user_id},
            )
            _record_event(user_id, "user_data_deleted")
            return _json_response(200, {"ok": True, "deleted": True})

        return _json_response(405, {"error": "method_not_allowed"})

    except TableMissing as exc:
        stage = "table_missing:" + exc.table_name
        print("PUT_K_SEBE_503 stage=%s method=%s path=%s" %
              (stage, locals().get("method", "UNKNOWN"),
               locals().get("path", "UNKNOWN")), flush=True)
        LOG.error("API request failed stage=%s method=%s path=%s",
                  stage, locals().get("method", "UNKNOWN"),
                  locals().get("path", "UNKNOWN"))
        return _json_response(503, {"error": "service_unavailable", "stage": stage})
    except ValueError as exc:
        return _json_response(401, {"error": str(exc)})
    except Exception as exc:
        # Never log request bodies, Telegram initData, credentials, or diary content.
        print("PUT_K_SEBE_503 stage=handler method=%s path=%s exception_type=%s" %
              (locals().get("method", "UNKNOWN"),
               locals().get("path", "UNKNOWN"), type(exc).__name__), flush=True)
        LOG.exception("API request failed method=%s path=%s",
                      locals().get("method", "UNKNOWN"),
                      locals().get("path", "UNKNOWN"))
        return _json_response(503, {"error": "service_unavailable"})
