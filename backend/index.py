import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from threading import RLock
from urllib.parse import parse_qsl

import ydb
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

_driver = None
_pool = None
_db_lock = RLock()
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
        pool.execute_with_retries(
            """
            CREATE TABLE IF NOT EXISTS premium_purchases (
                purchase_id Utf8 NOT NULL,
                user_id Utf8 NOT NULL,
                product_id Utf8 NOT NULL,
                duration_days Uint32 NOT NULL,
                purchased_at Utf8 NOT NULL,
                status Utf8 NOT NULL,
                PRIMARY KEY (purchase_id)
            );
            """
        )
        pool.execute_with_retries(
            """
            CREATE TABLE IF NOT EXISTS premium_trials (
                user_id Utf8 NOT NULL,
                started_at Utf8 NOT NULL,
                expires_at Utf8 NOT NULL,
                PRIMARY KEY (user_id)
            );
            """
        )
        pool.execute_with_retries(
            """
            CREATE TABLE IF NOT EXISTS ai_analyses (
                user_id Utf8 NOT NULL,
                entry_id Utf8 NOT NULL,
                analysis_text Utf8 NOT NULL,
                input_tokens Uint64 NOT NULL,
                output_tokens Uint64 NOT NULL,
                created_at Utf8 NOT NULL,
                PRIMARY KEY (user_id, entry_id)
            );
            """
        )
        _schema_ready = True


def _record_event(user_id, event_name, metadata=None):
    pool = _db()
    analytics_user_id = hmac.new(
        ANALYTICS_SECRET.encode("utf-8"),
        ("put-k-sebe-analytics:" + str(user_id)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
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
            "$user_id": analytics_user_id,
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
            "Access-Control-Allow-Methods": "GET,PUT,DELETE,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,X-Telegram-Init-Data,X-Admin-Password",
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
    practice_history = value.get("practiceHistory", [])
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
    if not isinstance(chat, list) or len(chat) > 300:
        raise ValueError("Invalid assistant chat history")
    if onboarding is not None and not isinstance(onboarding, dict):
        raise ValueError("Invalid onboarding data")

    clean = {
        "entries": entries,
        "patterns": patterns,
        "completed": completed,
        "practiceHistory": practice_history,
        "chat": chat,
        "onboarding": onboarding,
    }
    # Keep optional application metadata, but do not accept arbitrary top-level keys.
    for key in ("settings", "meta", "aiProfile"):
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
    result = _db().execute_with_retries(
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
    trial_result = _db().execute_with_retries(
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
    active = paid_active or trial_active
    premium_until = expiry if paid_active else None
    if trial_active:
        trial_expiry = datetime.fromisoformat(trial["expires_at"].replace("Z", "+00:00"))
        if premium_until is None or trial_expiry > premium_until:
            premium_until = trial_expiry
    return {
        "active": active,
        "premium_until": premium_until.isoformat() if premium_until else None,
        "source": "paid" if paid_active else ("trial" if trial_active else None),
        "trial_used": trial is not None,
        "trial_active": trial_active,
    }


def _start_trial(user_id):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    started = now.isoformat()
    expires = (now + timedelta(days=7)).isoformat()
    try:
        _db().execute_with_retries(
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
        _ensure_schema()

        if name == "new_digital_product":
            product_id = str(payload.get("product_id", ""))
            duration = TRIBUTE_PRODUCTS.get(product_id)
            user_id = str(int(payload.get("telegram_user_id", 0)))
            purchased_at = str(payload.get("purchase_created_at") or event.get("created_at") or "")
            if not duration or user_id == "0" or not purchased_at:
                raise ValueError("Unknown product or missing purchase data")
            existing = _db().execute_with_retries(
                """
                DECLARE $purchase_id AS Utf8;
                SELECT purchase_id FROM premium_purchases WHERE purchase_id = $purchase_id;
                """,
                parameters={"$purchase_id": purchase_id},
            )
            for result_set in existing:
                if result_set.rows:
                    return _json_response(200, {"ok": True, "duplicate": True})
            _db().execute_with_retries(
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
            return _json_response(200, {"ok": True})

        if name == "digital_product_refunded":
            existing = _db().execute_with_retries(
                """
                DECLARE $purchase_id AS Utf8;
                SELECT purchase_id FROM premium_purchases WHERE purchase_id = $purchase_id;
                """,
                parameters={"$purchase_id": purchase_id},
            )
            found = any(result_set.rows for result_set in existing)
            if not found:
                return _json_response(503, {"error": "purchase_not_found_yet"})
            _db().execute_with_retries(
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


def _deepseek_chat(messages, max_tokens=3000, timeout=55):
    import urllib.request
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + DEEPSEEK_API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _deepseek_analyze_entry(user_id, entry_id):
    from datetime import datetime, timezone
    import urllib.error
    stage = "load_user_state"
    _ensure_schema()
    state = _read_state(user_id)
    if not isinstance(state, dict):
        return _json_response(404, {"error": "user_state_not_found"})
    entries = state.get("entries") or []
    entry = next((item for item in entries if str(item.get("id")) == entry_id), None)
    if not isinstance(entry, dict):
        return _json_response(404, {"error": "entry_not_found"})
    # Namespace cache by model so old OpenAI analyses are not returned as DeepSeek results.
    cache_entry_id = "deepseek-flash:" + entry_id
    stage = "read_analysis_cache"
    cached = _db().execute_with_retries(
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
            })

    from datetime import datetime, timezone
    used_today, _ = _daily_ai_usage(user_id, datetime.now(timezone.utc).date().isoformat())
    if used_today >= 5:
        return _json_response(429, {"error":"daily_analysis_limit","limit":5,"used":used_today,
                                    "message":"На сегодня использованы все 5 индивидуальных анализов."})
    stage = "prepare_prompt"
    fields = [
        ("Ситуация", "situation"), ("Эмоция", "emotion"),
        ("Интенсивность эмоции (0–10)", "intensity"),
        ("Автоматическая мысль", "thought"), ("Факты за мысль", "forText"),
        ("Факты против мысли", "against"), ("Более сбалансированный взгляд", "balanced"),
        ("Следующий небольшой шаг", "action"),
    ]
    entry_text = "\n".join(label + ": " + str(entry.get(key, ""))[:1800] for label, key in fields)
    system_prompt = (
        "Ты — Персональный аналитик приложения «Путь к себе». "
        "Помогай человеку в рамках навыков КПТ и бережной саморефлексии. "
        "Отвечай по-русски, уважительно, конкретно и без шаблонных комплиментов. "
        "Не ставь диагнозов, не делай выводов о личности по одной записи, не выдавай гипотезы за факты. "
        "Не навязывай позитивное мышление. Если данных недостаточно, прямо скажи об этом. "
        "Не ищи когнитивное искажение там, где его нет. "
        "Структура ответа: 1) Что видно в ситуации. "
        "2) Возможная автоматическая мысль и когнитивное искажение (только если есть основания; "
        "иначе напиши, что явного искажения не видно). "
        "3) Что запись может упускать или какие вопросы стоит себе задать. "
        "4) Более точный, сбалансированный взгляд. 5) Один небольшой практический шаг. "
        "Не более 350 слов. Это поддержка для самонаблюдения, не психотерапия и не медицинская помощь."
    )
    try:
        stage = "request_deepseek"
        data = _deepseek_chat(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": entry_text}],
            max_tokens=3000,
            timeout=55,
        )
        stage = "parse_model_response"
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices else {}
        analysis = str((message or {}).get("content") or "").strip()
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
    except Exception as exc:
        LOG.exception("DeepSeek analysis failed stage=%s type=%s", stage, type(exc).__name__)
        return _json_response(502, {
            "error": "deepseek_analysis_failed",
            "stage": stage,
            "detail": stage + ": " + type(exc).__name__,
        })



def _daily_ai_usage(user_id, day_key):
    result = _db().execute_with_retries(
        """
        DECLARE $user_id AS Utf8;
        SELECT entry_id, created_at FROM ai_analyses WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id},
    )
    count = 0
    summary_exists = False
    for result_set in result:
        for row in result_set.rows:
            entry_id, created_at = str(row.entry_id), str(row.created_at)
            if created_at.startswith(day_key):
                if entry_id.startswith("daily-summary:"):
                    summary_exists = True
                else:
                    count += 1
    return count, summary_exists


def _save_ai_result(user_id, cache_key, text_value, usage, created):
    _db().execute_with_retries(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $entry_id AS Utf8;
        DECLARE $analysis_text AS Utf8;
        DECLARE $input_tokens AS Uint64;
        DECLARE $output_tokens AS Uint64;
        DECLARE $created_at AS Utf8;
        UPSERT INTO ai_analyses
            (user_id, entry_id, analysis_text, input_tokens, output_tokens, created_at)
        VALUES
            ($user_id, $entry_id, $analysis_text, $input_tokens, $output_tokens, $created_at);
        """,
        parameters={
            "$user_id": user_id, "$entry_id": cache_key, "$analysis_text": text_value,
            "$input_tokens": int(usage.get("input_tokens", 0)),
            "$output_tokens": int(usage.get("output_tokens", 0)), "$created_at": created,
        },
    )


def _deepseek_daily_summary(user_id):
    from datetime import datetime, timezone
    import urllib.error
    now = datetime.now(timezone.utc)
    day_key = now.date().isoformat()
    cache_key = "daily-summary:" + day_key
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
            if entry_date.astimezone(timezone.utc).date() == now.date():
                todays.append(item)
        except (ValueError, TypeError):
            continue
    if not todays:
        return _json_response(400, {"error": "no_entries_today", "message": "Сегодня пока нет записей для сводки."})
    cached = _db().execute_with_retries(
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
            return _json_response(200, {"ok": True, "cached": True, "summary": str(row.analysis_text),
                "usage": {"input_tokens": int(row.input_tokens), "output_tokens": int(row.output_tokens)},
                "created_at": str(row.created_at)})
    snippets = []
    for i, entry in enumerate(todays[-30:], 1):
        fields = [("Ситуация","situation"),("Эмоция","emotion"),("Интенсивность","intensity"),
                  ("Автоматическая мысль","thought"),("Факты за","forText"),("Факты против","against"),
                  ("Сбалансированный взгляд","balanced"),("Следующий шаг","action")]
        snippets.append("Запись " + str(i) + ":\n" + "\n".join(
            label + ": " + str(entry.get(key, ""))[:900] for label, key in fields))
    system_prompt = (
        "Ты — бережный аналитик приложения «Путь к себе», работающий в рамках КПТ и саморефлексии. "
        "Составь дневную сводку по записям одного человека. Не ставь диагнозов и не делай выводов о личности. "
        "Отделяй факты от гипотез; не выдумывай повторения. На русском, до 500 слов. "
        "Структура: 1) картина дня; 2) повторяющиеся темы (только подтверждённые); "
        "3) связи ситуаций, мыслей и реакций; 4) что уже получилось; 5) один вопрос для наблюдения; "
        "6) не более 3 возможных паттернов для проверки с указанием подтверждающих записей. "
        "Назови их гипотезами и подчеркни, что пользователь сам решает, добавлять ли их."
    )
    try:
        data = _deepseek_chat([{"role":"system","content":system_prompt},
            {"role":"user","content":"Записи за "+day_key+":\n\n"+"\n\n".join(snippets)}],
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
        return _json_response(200, {"ok":True,"cached":False,"summary":summary,"usage":usage,"created_at":created})
    except urllib.error.HTTPError as exc:
        LOG.error("DeepSeek daily summary HTTP error status=%s", exc.code)
        return _json_response(502, {"error":"deepseek_provider_error","provider_status":int(exc.code)})
    except urllib.error.URLError as exc:
        LOG.error("DeepSeek daily summary network error: %s", type(exc.reason).__name__)
        return _json_response(502, {"error":"deepseek_network_error"})
    except Exception as exc:
        LOG.exception("DeepSeek daily summary failed type=%s", type(exc).__name__)
        return _json_response(502, {"error":"deepseek_analysis_failed","detail":type(exc).__name__})


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


def handler(event, context):
    try:
        method, path, headers, body = _request_parts(event)

        if method == "OPTIONS":
            return _json_response(204, {"ok": True})

        if path == "/health" and method == "GET":
            _ensure_schema()
            return _json_response(200, {"ok": True, "service": "put-k-sebe-api"})

        if path == "/v1/webhooks/tribute" and method == "POST":
            return _handle_tribute_webhook(
                body.encode("utf-8"), headers.get("trbt-signature", "")
            )

        if path == "/v1/premium" and method == "GET":
            user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
            _ensure_schema()
            return _json_response(200, _premium_status(user_id))

        if path == "/v1/trial" and method == "POST":
            user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
            _ensure_schema()
            return _start_trial(user_id)
        if path == "/v1/ai/test" and method == "POST":
            user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
            _ensure_schema()
            if not _premium_status(user_id).get("active"):
                return _json_response(403, {"error": "premium_required"})
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error": "deepseek_not_configured"})
            return _deepseek_connection_test()
        if path == "/v1/ai/daily-summary" and method == "POST":
            user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
            _ensure_schema()
            if not _premium_status(user_id).get("active"):
                return _json_response(403, {"error":"premium_required"})
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error":"deepseek_not_configured"})
            return _deepseek_daily_summary(user_id)

        if path == "/v1/ai/analyze-entry" and method == "POST":
            user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
            _ensure_schema()
            if not _premium_status(user_id).get("active"):
                return _json_response(403, {"error": "premium_required"})
            if not DEEPSEEK_API_KEY:
                return _json_response(503, {"error": "deepseek_not_configured"})
            try:
                payload = json.loads(body or "{}")
                entry_id = str(payload.get("entry_id", "")).strip()
                if not entry_id or len(entry_id) > 128:
                    raise ValueError("invalid_entry_id")
            except (json.JSONDecodeError, ValueError, TypeError):
                return _json_response(400, {"error": "invalid_request"})
            return _deepseek_analyze_entry(user_id, entry_id)

        if path == "/v1/events" and method == "POST":
            user_id = _telegram_user(headers.get("x-telegram-init-data", ""))
            try:
                payload = json.loads(body or "{}")
                event_name = payload.get("event")
                allowed = {
                    "app_open", "onboarding_started", "onboarding_completed",
                    "diary_entry_created", "practice_opened", "practice_completed",
                }
                if event_name not in allowed:
                    raise ValueError("Invalid event")
                _ensure_schema()
                _record_event(user_id, event_name)
                return _json_response(202, {"ok": True})
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                return _json_response(400, {"error": str(exc)})

        if path == "/v1/admin/analytics" and method == "GET":
            if not ADMIN_PASSWORD or not hmac.compare_digest(
                headers.get("x-admin-password", ""), ADMIN_PASSWORD
            ):
                return _json_response(401, {"error": "unauthorized"})
            _ensure_schema()
            result = _db().execute_with_retries(
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
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except Exception:
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

        user_id = _telegram_user(headers.get("x-telegram-init-data", ""))

        if method == "GET":
            _ensure_schema()
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
