#!/usr/bin/env python3
"""Simulate the 23:59 summary job with YDB and the Telegram API mocked.

The bot token below is a fixture. Run from the repository root:

    python3 backend/test_daily_summary_notify.py
"""

import io
import json
import logging
import os
import sys
import time
import types
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_TOKEN = "0000000000:TEST_NOT_A_REAL_TOKEN"
READY_TEXT = """\
✨ Твои итоги дня готовы!

Мы разобрали твои записи за сегодня: какие мысли повторялись, что удалось заметить и какие небольшие шаги могут помочь завтра.

👇 Нажми «Открыть дневник», чтобы посмотреть.\
"""
EMPTY_TEXT = """\
🌙 Сегодня в дневнике не появилось новых записей, поэтому итоги дня подвести не получилось.

Перемены в мышлении складываются из маленьких шагов каждый день. Даже одна короткая запись или пять минут практики помогают лучше понять себя и заметить мысли, которые тянут назад.

Не пропускай дни, твой прогресс собирается из них 🌿

👇 Нажми «Открыть дневник» и добавь запись уже завтра или прямо сейчас.\
"""
# 20:59 UTC is 23:59 in Europe/Moscow and 15:59 in New York.
DUE_NOW = datetime(2026, 11, 14, 20, 59, tzinfo=timezone.utc)


def _assert(condition, message):
    if not condition:
        raise SystemExit("FAIL: " + message)


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Result:
    def __init__(self, rows):
        self.rows = rows


class _DB:
    def __init__(self, consents):
        self.consents = consents
        self.claims = set()
        self.deletes = []
        self.queries = []

    def execute(self, query, parameters=None, table=None):
        compact = " ".join(query.split())
        self.queries.append(compact)
        parameters = parameters or {}
        if "GROUP BY timezone" in compact:
            return [_Result([
                _Row(timezone="Europe/Moscow"),
                _Row(timezone="America/New_York"),
            ])]
        if "SELECT user_id FROM user_profiles" in compact:
            tz_name = parameters.get("$timezone")
            if tz_name == "Europe/Moscow":
                rows = [_Row(user_id=user_id) for user_id in ("42", "43", "44", "45", "46")]
            elif tz_name == "America/New_York":
                rows = [_Row(user_id="99")]
            else:
                rows = []
            return [_Result(rows)]
        if "FROM user_consents" in compact:
            user_id = parameters.get("$user_id")
            if self.consents.get(user_id):
                return [_Result([_Row(
                    ai_processing=True,
                    ai_history_storage=True,
                    consent_version="test",
                    updated_at="2026-11-14T00:00:00+00:00",
                )])]
            return [_Result([])]
        if "INSERT INTO daily_summary_claims" in compact:
            key = (parameters.get("$user_id"), parameters.get("$day_key"))
            if key in self.claims:
                raise RuntimeError("duplicate claim")
            self.claims.add(key)
            return []
        if "DELETE FROM daily_summary_claims" in compact:
            key = (parameters.get("$user_id"), parameters.get("$day_key"))
            self.deletes.append(key)
            self.claims.discard(key)
            return []
        raise AssertionError("unexpected query: " + compact[:180])


def _stub_ydb():
    ydb = types.ModuleType("ydb")
    iam = types.ModuleType("ydb.iam")
    iam.MetadataUrlCredentials = lambda: None
    ydb.iam = iam
    ydb.Driver = lambda *args, **kwargs: None
    ydb.QuerySessionPool = lambda driver: None
    ydb.TypedValue = lambda value, type_: (value, type_)
    ydb.PrimitiveType = types.SimpleNamespace(Uint64="Uint64")
    sys.modules["ydb"] = ydb
    sys.modules["ydb.iam"] = iam


def _load_index():
    os.environ["YDB_ENDPOINT"] = "grpcs://ydb.example:2135"
    os.environ["YDB_DATABASE"] = "/local/test"
    os.environ["BOT_TOKEN"] = FIXTURE_TOKEN
    _stub_ydb()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import index
    return index


def _response(status, payload):
    return {
        "statusCode": status,
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _install_job(index, db, summaries, sender):
    index.TELEGRAM_SEND_INTERVAL_SECONDS = 0
    index._last_telegram_send_at = 0.0
    index._execute = db.execute
    index._deepseek_daily_summary = lambda user_id, tz_name, force=False: summaries[user_id]
    index._send_telegram_message = sender


def main():
    index = _load_index()
    original_send = index._send_telegram_message
    _assert(index.SUMMARY_READY_TEXT == READY_TEXT, "ready text drifted")
    _assert(index.SUMMARY_EMPTY_TEXT == EMPTY_TEXT, "empty text drifted")
    _assert(index.TELEGRAM_SEND_INTERVAL_SECONDS <= 0.05, "send interval is not under 25/s")
    ready = index.build_summary_notification("ready", 42)
    empty = index.build_summary_notification("empty", 44)
    for payload, text, blank_lines in ((ready, READY_TEXT, 2), (empty, EMPTY_TEXT, 3)):
        _assert("parse_mode" not in payload, "parse_mode must be absent")
        _assert(payload["text"] == text, "notification text")
        # Draft '\n\n' is a blank line: two newline characters, not backslash + n.
        _assert(payload["text"].count("\n\n") == blank_lines, "missing real paragraph breaks")
        _assert("\\" + "n" not in payload["text"], "literal backslash-n in notification text")
        encoded = json.dumps(payload["text"], ensure_ascii=False)
        _assert("\\n\\n" in encoded, "JSON payload must encode real newlines")
        _assert("\\\\n" not in encoded, "JSON payload encodes a literal backslash-n")
        button = payload["reply_markup"]["inline_keyboard"][0][0]
        _assert(button["text"] == "Открыть дневник", "button text")
        _assert(button["web_app"]["url"] == "https://oleggrigorev131313.github.io/put-k-sebe/", "mini app url")
        _assert(FIXTURE_TOKEN not in json.dumps(payload), "token in payload")

    sent = []

    def sender(method, payload):
        _assert(method == "sendMessage", "method")
        _assert(FIXTURE_TOKEN not in json.dumps(payload), "token in send")
        sent.append(payload)
        return "ok"

    summaries = {
        "42": _response(200, {"ok": True, "summary": "Сводка дня"}),
        "44": _response(400, {"error": "no_entries_today"}),
        "45": _response(422, {"error": "no_meaningful_entries"}),
        "46": _response(409, {"error": "daily_summary_already_used"}),
        "99": _response(200, {"ok": True, "summary": "Не должно отправиться"}),
    }
    consents = {"42": True, "43": False, "44": True, "45": True, "46": True, "99": True}
    db = _DB(consents)
    _install_job(index, db, summaries, sender)
    index._run_due_daily_summaries(DUE_NOW)
    by_chat = {item["chat_id"]: item["text"] for item in sent}
    _assert(set(by_chat) == {42, 44, 46}, "unexpected recipients: " + str(sorted(by_chat)))
    _assert(by_chat[42] == READY_TEXT, "ready message")
    _assert(by_chat[44] == EMPTY_TEXT, "empty message")
    _assert(by_chat[46] == READY_TEXT, "already-used summary still notifies once")
    _assert(by_chat[42].count("\n\n") == 2 and "\\" + "n" not in by_chat[42], "sent ready text paragraphs")
    _assert(by_chat[44].count("\n\n") == 3 and "\\" + "n" not in by_chat[44], "sent empty text paragraphs")
    _assert(all(key[1].startswith("notify:") for key in db.claims), "claim key must be notify-prefixed")
    _assert(("42", "notify:2026-11-14") in db.claims, "ready claim")
    _assert(("43", "notify:2026-11-14") not in db.claims, "no consent must not claim")
    _assert(("45", "notify:2026-11-14") not in db.claims, "fragmentary day is not a notification")
    _assert(("99", "notify:2026-11-14") not in db.claims, "other timezone")
    sent.clear()
    index._run_due_daily_summaries(DUE_NOW)
    _assert(sent == [], "second run in the same local day must not send")

    blocked = []

    def blocked_sender(method, payload):
        blocked.append(payload["chat_id"])
        return "blocked"

    db.claims.discard(("42", "notify:2026-11-14"))
    _install_job(index, db, summaries, blocked_sender)
    index._run_due_daily_summaries(DUE_NOW)
    _assert(blocked == [42], "blocked send happens once")
    _assert(("42", "notify:2026-11-14") in db.claims, "403 keeps the daily claim")
    _assert(db.deletes == [], "403 must not release the claim")
    blocked.clear()
    index._run_due_daily_summaries(DUE_NOW)
    _assert(blocked == [], "403 is not retried the same day")

    attempts = []

    def flaky_sender(method, payload):
        attempts.append(payload["chat_id"])
        if len(attempts) == 1:
            return "error"
        return "ok"

    db.claims.discard(("44", "notify:2026-11-14"))
    summaries["42"] = _response(422, {"error": "no_meaningful_entries"})
    summaries["46"] = _response(422, {"error": "no_meaningful_entries"})
    _install_job(index, db, summaries, flaky_sender)
    index._run_due_daily_summaries(DUE_NOW)
    _assert(attempts == [44], "transient failure sends once")
    _assert(("44", "notify:2026-11-14") not in db.claims, "transient failure releases the claim")
    index._run_due_daily_summaries(DUE_NOW)
    _assert(attempts == [44, 44], "released claim can send on the next run")
    _assert(("44", "notify:2026-11-14") in db.claims, "successful retry keeps the claim")

    sleeps = []
    original_sleep = time.sleep
    time.sleep = lambda seconds: sleeps.append(seconds)
    try:
        index.TELEGRAM_SEND_INTERVAL_SECONDS = 0.05
        index._last_telegram_send_at = 0.0
        pace_db = _DB({"1": True, "2": True, "3": True})

        def pace_execute(query, parameters=None, table=None):
            compact = " ".join(query.split())
            parameters = parameters or {}
            if "GROUP BY timezone" in compact:
                return [_Result([_Row(timezone="Europe/Moscow")])]
            if "SELECT user_id FROM user_profiles" in compact:
                return [_Result([_Row(user_id=user_id) for user_id in ("1", "2", "3")])]
            if "FROM user_consents" in compact:
                return [_Result([_Row(
                    ai_processing=True, ai_history_storage=False,
                    consent_version="test", updated_at="2026-11-14T00:00:00+00:00",
                )])]
            if "INSERT INTO daily_summary_claims" in compact:
                return []
            raise AssertionError(compact[:160])

        index._execute = pace_execute
        index._deepseek_daily_summary = lambda user_id, tz_name, force=False: _response(
            400, {"error": "no_entries_today"}
        )
        index._send_telegram_message = lambda method, payload: "ok"
        index._run_due_daily_summaries(DUE_NOW)
        _assert(len(sleeps) == 2, "pace sleeps between sends, got %s" % len(sleeps))
        _assert(all(0 < delay <= 0.05 for delay in sleeps), "sleep exceeds the 20/s gap")
    finally:
        time.sleep = original_sleep
        index.TELEGRAM_SEND_INTERVAL_SECONDS = 0

    index._send_telegram_message = original_send
    original_open = index._telegram_urlopen
    logs = _LogCapture()
    index.LOG.addHandler(logs)
    try:
        def blocked_open(request):
            raise urllib.error.HTTPError(
                "https://relay.example.test/sendMessage", 403, "Forbidden", {},
                io.BytesIO(b'{"ok":false,"error_code":403,"description":"Forbidden: bot was blocked by the user"}'),
            )
        index._telegram_urlopen = blocked_open
        result = index._send_telegram_message("sendMessage", ready)
        _assert(result == "blocked", "HTTP 403")
        logged = "\n".join(logs.messages)
        _assert(FIXTURE_TOKEN not in logged, "token leaked into logs")
        _assert("api.telegram.org" not in logged, "request URL leaked into logs")
        _assert("403" in logged, "status is logged")

        hosts = []

        def grab(request):
            from urllib.parse import urlparse
            hosts.append(urlparse(request.full_url).hostname)
            class _Response:
                def read(self, n=-1):
                    return b'{"ok":true,"result":{}}'
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return False
            return _Response()

        os.environ["TELEGRAM_API_BASE"] = "https://relay.example.test/"
        index._telegram_urlopen = grab
        try:
            relay_result = index._send_telegram_message("sendMessage", {"chat_id": 1, "text": "hi"})
        finally:
            os.environ.pop("TELEGRAM_API_BASE", None)
        _assert(relay_result == "ok", "relay send")
        _assert(hosts == ["relay.example.test"], "TELEGRAM_API_BASE host")
        _assert(index._telegram_api_base() == "https://api.telegram.org", "default API base")

        os.environ["TELEGRAM_API_BASE"] = "https://127.0.0.1:9"
        index._telegram_urlopen = original_open
        try:
            refused = index._send_telegram_message("sendMessage", {"chat_id": 1, "text": "hi"})
        finally:
            os.environ.pop("TELEGRAM_API_BASE", None)
            index._telegram_urlopen = original_open
        _assert(refused == "network", "connection refused is a network failure")
        _assert(FIXTURE_TOKEN not in "\n".join(logs.messages), "token leaked after network failure")
    finally:
        index._telegram_urlopen = original_open
        index.LOG.removeHandler(logs)

    calls = []

    def network_down(request):
        calls.append(1)
        raise urllib.error.URLError("timed out")

    index.TELEGRAM_SEND_INTERVAL_SECONDS = 0
    index._telegram_consecutive_network_failures = 0
    index._telegram_urlopen = network_down
    index._execute = _DB({"1": True, "2": True, "3": True, "4": True}).execute

    def network_execute(query, parameters=None, table=None):
        compact = " ".join(query.split())
        parameters = parameters or {}
        if "GROUP BY timezone" in compact:
            return [_Result([_Row(timezone="Europe/Moscow")])]
        if "SELECT user_id FROM user_profiles" in compact:
            return [_Result([_Row(user_id=user_id) for user_id in ("1", "2", "3", "4")])]
        if "FROM user_consents" in compact:
            return [_Result([_Row(
                ai_processing=True, ai_history_storage=False,
                consent_version="test", updated_at="2026-11-14T00:00:00+00:00",
            )])]
        if "INSERT INTO daily_summary_claims" in compact or "DELETE FROM daily_summary_claims" in compact:
            return []
        raise AssertionError(compact[:160])

    index._execute = network_execute
    index._deepseek_daily_summary = lambda user_id, tz_name, force=False: _response(
        400, {"error": "no_entries_today"}
    )
    index._send_telegram_message = original_send
    try:
        index._run_due_daily_summaries(DUE_NOW)
        _assert(len(calls) == index.TELEGRAM_MAX_CONSECUTIVE_NETWORK_FAILURES, "stop after consecutive network failures")
        _assert(index._telegram_consecutive_network_failures == index.TELEGRAM_MAX_CONSECUTIVE_NETWORK_FAILURES, "failure counter")
    finally:
        index._telegram_urlopen = original_open

    called = []
    original_job = index._run_due_daily_summaries
    index._run_due_daily_summaries = lambda now=None: called.append("ran")
    try:
        response = index.handler({
            "messages": [{"details": {"payload": json.dumps({"job": "daily_summary"})}}],
        }, None)
        _assert(response["statusCode"] == 200 and called == ["ran"], "timer payload reaches the job")
        called.clear()
        response = index.handler({
            "messages": [{"details": {"payload": "{}"}}],
        }, None)
        _assert(response["statusCode"] == 200 and called == [], "other timer jobs stay quiet")
        state_response = index.handler({
            "httpMethod": "GET", "path": "/v1/state", "headers": {}, "body": "",
        }, None)
        _assert(state_response["statusCode"] == 401, "state route")
    finally:
        index._run_due_daily_summaries = original_job

    print(json.dumps({
        "ready": ready,
        "empty": empty,
        "idempotency_key_example": "notify:2026-11-14",
        "send_interval_seconds": 0.05,
    }, ensure_ascii=False, indent=2))
    print("ok")


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


if __name__ == "__main__":
    main()
