#!/usr/bin/env python3
"""Create only missing YDB tables for Put-k-sebe.

Does not ALTER, DROP, or rewrite existing tables. Existing rows stay in place.
Credentials: YC_IAM_TOKEN / IAM_TOKEN, otherwise `yc iam create-token`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import ydb

TABLES = (
    "user_state",
    "analytics_events",
    "user_profiles",
    "premium_purchases",
    "premium_trials",
    "ai_analyses",
    "user_consents",
    "daily_summary_claims",
    "ai_usage_events",
)

# Same columns and primary keys as the application reads and writes.
DDL = {
    "user_state": """
        CREATE TABLE user_state (
            user_id Utf8 NOT NULL,
            state_json Utf8 NOT NULL,
            updated_at Timestamp,
            PRIMARY KEY (user_id)
        );
    """,
    "analytics_events": """
        CREATE TABLE analytics_events (
            event_id Utf8 NOT NULL,
            user_id Utf8 NOT NULL,
            event_name Utf8 NOT NULL,
            created_at Timestamp,
            metadata_json Utf8,
            PRIMARY KEY (event_id)
        );
    """,
    "user_profiles": """
        CREATE TABLE user_profiles (
            user_id Utf8 NOT NULL, username Utf8, first_name Utf8, last_name Utf8,
            timezone Utf8, created_at Utf8 NOT NULL, last_seen Utf8 NOT NULL,
            PRIMARY KEY (user_id)
        );
    """,
    "premium_purchases": """
        CREATE TABLE premium_purchases (
            purchase_id Utf8 NOT NULL,
            user_id Utf8 NOT NULL,
            product_id Utf8 NOT NULL,
            duration_days Uint32 NOT NULL,
            purchased_at Utf8 NOT NULL,
            status Utf8 NOT NULL,
            PRIMARY KEY (purchase_id)
        );
    """,
    "premium_trials": """
        CREATE TABLE premium_trials (
            user_id Utf8 NOT NULL,
            started_at Utf8 NOT NULL,
            expires_at Utf8 NOT NULL,
            PRIMARY KEY (user_id)
        );
    """,
    "ai_analyses": """
        CREATE TABLE ai_analyses (
            user_id Utf8 NOT NULL,
            entry_id Utf8 NOT NULL,
            analysis_text Utf8 NOT NULL,
            input_tokens Uint64 NOT NULL,
            output_tokens Uint64 NOT NULL,
            created_at Utf8 NOT NULL,
            PRIMARY KEY (user_id, entry_id)
        );
    """,
    "user_consents": """
        CREATE TABLE user_consents (
            user_id Utf8 NOT NULL,
            ai_processing Bool NOT NULL,
            ai_history_storage Bool NOT NULL,
            consent_version Utf8 NOT NULL,
            updated_at Utf8 NOT NULL,
            PRIMARY KEY (user_id)
        );
    """,
    "daily_summary_claims": """
        CREATE TABLE daily_summary_claims (
            user_id Utf8 NOT NULL,
            day_key Utf8 NOT NULL,
            created_at Utf8 NOT NULL,
            PRIMARY KEY (user_id, day_key)
        );
    """,
    "ai_usage_events": """
        CREATE TABLE ai_usage_events (
            event_id Utf8 NOT NULL,
            user_id Utf8 NOT NULL,
            feature Utf8 NOT NULL,
            request_key Utf8 NOT NULL,
            created_at Utf8 NOT NULL,
            PRIMARY KEY (event_id)
        );
    """,
}

PAUSE_SECONDS = 1.5
BACKOFF_SECONDS = (2, 4, 8, 16, 32)


def _iam_token():
    for key in ("YC_IAM_TOKEN", "IAM_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    proc = subprocess.run(
        ["yc", "iam", "create-token"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        raise SystemExit("Не удалось получить IAM-токен (yc iam create-token): " + detail)
    token = (proc.stdout or "").strip()
    if not token:
        raise SystemExit("yc iam create-token вернул пустой токен")
    return token


def _credentials(token):
    factory = getattr(ydb, "AccessTokenCredentials", None)
    if factory is None:
        factory = ydb.credentials.AccessTokenCredentials
    return factory(token)


def _entry_name(child):
    name = str(getattr(child, "name", "") or "")
    return name.rstrip("/").split("/")[-1]


def _entry_kind(child):
    kind = getattr(child, "type", None)
    return str(getattr(kind, "name", kind))


def _list_tables(driver, database):
    directory = driver.scheme_client.list_directory(database)
    found = {}
    for child in getattr(directory, "children", None) or []:
        short = _entry_name(child)
        if short:
            found[short] = _entry_kind(child)
    return found


def _is_table_kind(kind):
    text = (kind or "").upper()
    return "TABLE" in text


def _describe(table_pool, database, name):
    full = database.rstrip("/") + "/" + name
    try:
        with table_pool.checkout() as session:
            desc = session.describe_table(full)
    except Exception as exc:
        return {"describe_error": " ".join(str(exc).split())[:400]}
    columns = []
    for col in getattr(desc, "columns", []) or []:
        columns.append({"name": str(col.name), "type": str(col.type)})
    return {
        "columns": columns,
        "primary_key": [str(item) for item in (getattr(desc, "primary_key", []) or [])],
    }


def _schema_limited(exc):
    raw = " ".join(str(exc).split()).lower()
    return "400080" in raw or "number of schema operations" in raw


def _already_exists(exc):
    raw = " ".join(str(exc).split()).lower()
    return "already exists" in raw or "path exist" in raw or "table name conflict" in raw


def _create_one(pool, name):
    ddl = DDL[name]
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        try:
            pool.execute_with_retries(ddl)
            return "created"
        except Exception as exc:
            if _already_exists(exc):
                print(f"  {name}: уже появилась параллельно, данные не изменялись")
                return "exists"
            if not _schema_limited(exc) or attempt >= len(BACKOFF_SECONDS):
                raise
            delay = BACKOFF_SECONDS[attempt]
            print(f"  {name}: квота scheme 400080, пауза {delay} с (попытка {attempt + 1})")
            time.sleep(delay)
    raise RuntimeError(f"не удалось создать {name}")


def main():
    parser = argparse.ArgumentParser(description="Идемпотентная миграция таблиц Put-k-sebe")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Только список: что есть и чего нет")
    mode.add_argument("--apply", action="store_true", help="Создать только отсутствующие таблицы")
    parser.add_argument("--endpoint", default=os.environ.get("YDB_ENDPOINT", ""))
    parser.add_argument("--database", default=os.environ.get("YDB_DATABASE", ""))
    args = parser.parse_args()

    endpoint = args.endpoint.strip().rstrip("/")
    database = args.database.strip()
    if database and not database.startswith("/"):
        database = "/" + database
    if not endpoint or not database:
        raise SystemExit("Нужны --endpoint и --database (или YDB_ENDPOINT и YDB_DATABASE)")

    driver = ydb.Driver(
        endpoint=endpoint,
        database=database,
        credentials=_credentials(_iam_token()),
    )
    table_pool = None
    query_pool = None
    try:
        driver.wait(fail_fast=True, timeout=15)
        table_pool = ydb.SessionPool(driver)
        existing = _list_tables(driver, database)
        print(f"База: {database}")
        print("Режим: " + ("dry-run" if args.dry_run else "apply"))
        missing = []
        for name in TABLES:
            kind = existing.get(name)
            if kind is None:
                print(f"{name}\tmissing")
                missing.append(name)
                continue
            if not _is_table_kind(kind):
                print(f"{name}\tblocked\tkind={kind}")
                if args.apply:
                    raise SystemExit(
                        f"{name} существует, но это не таблица ({kind}). Создание остановлено, объект не изменён."
                    )
                continue
            described = _describe(table_pool, database, name)
            if "describe_error" in described:
                print(f"{name}\texists\tdescribe_error={described['describe_error']}")
            else:
                cols = ",".join(col["name"] for col in described["columns"])
                pk = ",".join(described["primary_key"])
                print(f"{name}\texists\tcolumns={cols}\tpk={pk}")

        if args.dry_run:
            print(f"dry-run: отсутствует {len(missing)} из {len(TABLES)}. Изменений нет.")
            return 0

        if not missing:
            print("apply: все таблицы уже есть. CREATE не выполнялся.")
            return 0

        query_pool = ydb.QuerySessionPool(driver)
        for index, name in enumerate(missing):
            print(f"Создаю только отсутствующую таблицу {name}")
            _create_one(query_pool, name)
            print(f"{name}\tcreated")
            if index != len(missing) - 1:
                time.sleep(PAUSE_SECONDS)
        print(f"apply: создано {len(missing)}. Существующие таблицы не изменялись.")
        return 0
    finally:
        if query_pool is not None:
            query_pool.stop()
        if table_pool is not None:
            table_pool.stop()
        driver.stop()


if __name__ == "__main__":
    sys.exit(main())
