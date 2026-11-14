#!/usr/bin/env python3
"""Simulate a Telegram /start update and print the Bot API payload.

Telegram and YDB are not contacted. The bot token below is a fixture.
Run from the repository root:

    python3 backend/test_telegram_start.py
"""

import hashlib
import hmac
import io
import json
import logging
import os
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

FIXTURE_TOKEN = "0000000000:TEST_NOT_A_REAL_TOKEN"
SECRET_LABEL = b"put-k-sebe-telegram-webhook-v1"
EXPECTED_CAPTION = """\
Привет! 👋 На связи команда ПЕРЕСМОТР.

У каждого из нас есть мысли, которые повторяются снова и снова: «я недостаточно хорош», «у меня всегда всё идёт не так», «я должен быть лучше всех». Часто мы их даже не замечаем, а они годами влияют на настроение, решения и отношения.

ПЕРЕСМОТР помогает:
🔍 увидеть свои повторяющиеся паттерны мышления
✍️ разобрать, где мысль искажает реальность
🔄 переписать её на более честный и добрый взгляд на себя
📊 следить, как со временем меняется твоё состояние

Шаг за шагом ты отпускаешь убеждения, которые причиняют боль, и становишься той версией себя, которой хочешь быть.

👇 Нажми кнопку «Открыть дневник» под этим сообщением, и он откроется прямо в Telegram. Ничего скачивать не нужно.\
"""


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
    os.environ.pop("TELEGRAM_WEBHOOK_SECRET", None)
    _stub_ydb()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import index
    return index


def _derived_secret(token):
    return hmac.new(token.encode("utf-8"), SECRET_LABEL, hashlib.sha256).hexdigest()


class _Capture(list):
    def install(self):
        self._original = urllib.request.urlopen
        urllib.request.urlopen = self._urlopen
        return self

    def uninstall(self):
        urllib.request.urlopen = self._original

    def _urlopen(self, request, timeout=None):
        self.append({
            "url": request.full_url,
            "timeout": timeout,
            "body": json.loads(request.data.decode("utf-8")),
            "method_name": request.full_url.rsplit("/", 1)[-1],
        })
        response = io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8"))
        response.__enter__ = lambda: response
        response.__exit__ = lambda *args: False
        return response


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _event(text, secret, chat_id=4242):
    update = {
        "update_id": 100,
        "message": {
            "message_id": 7,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1700000000,
            "text": text,
        },
    }
    return {
        "httpMethod": "POST",
        "path": "/v1/webhooks/telegram",
        "headers": {"X-Telegram-Bot-Api-Secret-Token": secret},
        "body": json.dumps(update, ensure_ascii=False),
    }


def _assert(condition, message):
    if not condition:
        raise SystemExit("FAIL: " + message)


def main():
    index = _load_index()
    secret = _derived_secret(FIXTURE_TOKEN)
    _assert(index.TELEGRAM_WEBHOOK_SECRET_LABEL == SECRET_LABEL, "secret label drifted")
    _assert(index._expected_webhook_secret() == secret, "derived secret mismatch")
    _assert(index.WELCOME_CAPTION == EXPECTED_CAPTION, "welcome caption text drifted")
    units = index._telegram_text_units(index.WELCOME_CAPTION)
    codepoints = len(index.WELCOME_CAPTION)
    _assert(units <= index.TELEGRAM_CAPTION_LIMIT, "caption exceeds Telegram limit")
    _assert("parse_mode" not in json.dumps(index.build_start_reply(1)), "parse_mode must be absent")

    logs = _LogCapture()
    index.LOG.addHandler(logs)
    capture = _Capture().install()
    try:
        response = index.handler(_event("/start", secret), None)
        _assert(response["statusCode"] == 200, "start status")
        _assert(len(capture) == 1, "start should call Telegram once")
        sent = capture[0]
        _assert(sent["method_name"] == "sendPhoto", "method")
        _assert(FIXTURE_TOKEN not in json.dumps(sent["body"]), "token leaked into body")
        _assert(sent["body"]["chat_id"] == 4242, "chat id")
        _assert(sent["body"]["photo"].endswith("/assets/bot/welcome.jpg"), "photo url")
        _assert(sent["body"]["caption"] == EXPECTED_CAPTION, "caption")
        _assert("parse_mode" not in sent["body"], "parse_mode on wire")
        button = sent["body"]["reply_markup"]["inline_keyboard"][0][0]
        _assert(button["text"] == "Открыть дневник", "button text")
        _assert(button["web_app"]["url"] == "https://oleggrigorev131313.github.io/put-k-sebe/", "mini app url")

        capture.clear()
        response = index.handler(_event("/start promo_payload", secret, chat_id=-100), None)
        _assert(response["statusCode"] == 200, "payload start status")
        _assert(len(capture) == 1 and capture[0]["body"]["chat_id"] == -100, "payload chat")
        _assert(capture[0]["body"]["caption"] == EXPECTED_CAPTION, "payload caption")

        capture.clear()
        response = index.handler(_event("/start@kptprorabotkabot ref", secret), None)
        _assert(response["statusCode"] == 200 and len(capture) == 1, "mention start")

        capture.clear()
        response = index.handler(_event("/started", secret), None)
        _assert(response["statusCode"] == 200 and capture == [], "not a start command")

        capture.clear()
        response = index.handler(_event("/help", secret), None)
        _assert(response["statusCode"] == 200 and capture == [], "other command")

        group_event = _event("/start", secret)
        group_event["body"] = json.dumps({
            "update_id": 101,
            "message": {
                "message_id": 8,
                "chat": {"id": -50, "type": "group"},
                "text": "/start",
            },
        })
        capture.clear()
        response = index.handler(group_event, None)
        _assert(response["statusCode"] == 200 and capture == [], "group start is ignored")

        capture.clear()
        bad = _event("/start", "wrong-secret")
        response = index.handler(bad, None)
        _assert(response["statusCode"] == 401 and capture == [], "bad secret must not send")

        os.environ["TELEGRAM_WEBHOOK_SECRET"] = "override_secret_value"
        try:
            capture.clear()
            response = index.handler(_event("/start", secret), None)
            _assert(response["statusCode"] == 401 and capture == [], "derived secret ignored when override is set")
            response = index.handler(_event("/start", "override_secret_value"), None)
            _assert(response["statusCode"] == 200 and len(capture) == 1, "override secret")
        finally:
            os.environ.pop("TELEGRAM_WEBHOOK_SECRET", None)

        capture.clear()
        state_response = index.handler({
            "httpMethod": "GET",
            "path": "/v1/state",
            "headers": {},
            "body": "",
        }, None)
        _assert(state_response["statusCode"] == 401 and capture == [], "state route must not call Telegram")

        missing_response = index.handler({
            "httpMethod": "GET",
            "path": "/v1/does-not-exist",
            "headers": {},
            "body": "",
        }, None)
        _assert(missing_response["statusCode"] == 404 and capture == [], "unknown route")

        def explode(request, timeout=None):
            raise urllib.error.URLError("network down " + request.full_url)

        urllib.request.urlopen = explode
        response = index.handler(_event("/start", secret), None)
        _assert(response["statusCode"] == 200, "send failure still returns 200")
        logged = "\n".join(logs.messages)
        _assert(FIXTURE_TOKEN not in logged, "token leaked into logs")
        _assert("api.telegram.org" not in logged, "request URL leaked into logs")

        long_caption = "я" * (index.TELEGRAM_CAPTION_LIMIT + 1)
        split = index.build_start_reply(7, caption=long_caption)
        _assert([item[0] for item in split] == ["sendPhoto", "sendMessage"], "split methods")
        _assert("caption" not in split[0][1], "long caption stays off the photo")
        _assert(split[1][1]["text"] == long_caption, "split text")
        _assert(split[1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Открыть дневник", "split button")
        _assert("parse_mode" not in split[0][1] and "parse_mode" not in split[1][1], "split parse_mode")
    finally:
        capture.uninstall()
        index.LOG.removeHandler(logs)

    printable = {
        "caption_codepoints": codepoints,
        "caption_utf16_units": units,
        "caption_limit": index.TELEGRAM_CAPTION_LIMIT,
        "split": units > index.TELEGRAM_CAPTION_LIMIT,
        "calls": [{
            "method": sent["method_name"],
            "payload": sent["body"],
        }],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    print("ok")


if __name__ == "__main__":
    main()
