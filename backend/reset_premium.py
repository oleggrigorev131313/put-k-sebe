#!/usr/bin/env python3
"""One-off: сброс Premium/пробного периода у всех пользователей (без удаления строк).

Перед записью проверяет, что в premium_purchases нет активных оплат Tribute.
По умолчанию — dry-run; запись только с --yes.

Токен: YC_IAM_TOKEN / IAM_TOKEN или `yc iam create-token`.
Подключение: YDB_ENDPOINT и YDB_DATABASE (как в migrate.py).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

PAID_STATUS = "active"
EXPIRED_TRIAL_AT = "1970-01-01T00:00:00+00:00"


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
    import ydb

    factory = getattr(ydb, "AccessTokenCredentials", None)
    if factory is None:
        factory = ydb.credentials.AccessTokenCredentials
    return factory(token)


def paid_users_from_purchases(rows):
    """Distinct user_id with active Tribute purchase records (non-refundable status)."""
    users = set()
    for row in rows:
        if str(row.get("status", "")) == PAID_STATUS and row.get("user_id"):
            users.add(str(row["user_id"]))
    return users


def _collect_rows(result_sets):
    rows = []
    for result_set in result_sets or []:
        col_names = [str(c.name) for c in getattr(result_set, "columns", []) or []]
        for row in getattr(result_set, "rows", []) or []:
            item = {}
            for name in col_names:
                item[name] = str(getattr(row, name, "") or "")
            rows.append(item)
    return rows


def _fetch_purchases(pool):
    result = pool.execute_with_retries(
        """
        SELECT purchase_id, user_id, product_id, status
        FROM premium_purchases;
        """
    )
    return _collect_rows(result)


def _fetch_trials(pool):
    result = pool.execute_with_retries(
        """
        SELECT user_id, started_at, expires_at
        FROM premium_trials;
        """
    )
    return _collect_rows(result)


def _active_trial_users(trials, now):
    active = set()
    for row in trials:
        user_id = row.get("user_id")
        if not user_id:
            continue
        raw = str(row.get("expires_at", ""))
        try:
            exp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if exp > now:
            active.add(str(user_id))
    return active


def _expire_trial(pool, user_id, dry_run):
    if dry_run:
        return
    pool.execute_with_retries(
        """
        DECLARE $user_id AS Utf8;
        DECLARE $expires_at AS Utf8;
        UPDATE premium_trials
        SET expires_at = $expires_at
        WHERE user_id = $user_id;
        """,
        parameters={"$user_id": user_id, "$expires_at": EXPIRED_TRIAL_AT},
    )


def main():
    import ydb

    parser = argparse.ArgumentParser(description="Сброс Premium/пробного периода (YDB)")
    parser.add_argument("--endpoint", default=os.environ.get("YDB_ENDPOINT", ""))
    parser.add_argument("--database", default=os.environ.get("YDB_DATABASE", ""))
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Выполнить UPDATE (без флага — только dry-run)",
    )
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
    pool = None
    try:
        driver.wait(fail_fast=True, timeout=15)
        pool = ydb.QuerySessionPool(driver)
        now = datetime.now(timezone.utc)
        dry_run = not args.yes

        purchases = _fetch_purchases(pool)
        paid_users = paid_users_from_purchases(purchases)
        active_purchase_rows = sum(1 for p in purchases if str(p.get("status")) == PAID_STATUS)

        print(f"База: {database}")
        print("Режим: " + ("dry-run" if dry_run else "apply (--yes)"))
        print(f"Активных записей Tribute (premium_purchases.status={PAID_STATUS}): {active_purchase_rows}")
        print(f"Пользователей с оплаченным Premium: {len(paid_users)}")

        if len(paid_users) > 0:
            print(
                "ОТМЕНА: в базе есть реальные оплаты Tribute. "
                "Сброс не выполняется, данные не изменены."
            )
            return 2

        trials = _fetch_trials(pool)
        active_trial_users = _active_trial_users(trials, now)
        print(f"Строк в premium_trials: {len(trials)}")
        print(f"Сейчас с активным пробным периодом: {len(active_trial_users)}")
        print(
            f"Будет обновлено строк premium_trials (expires_at → прошлое): {len(trials)}"
        )

        if dry_run:
            print("dry-run: UPDATE не выполнялся. Для записи добавьте --yes")
            return 0

        updated = 0
        for row in trials:
            user_id = str(row.get("user_id", "")).strip()
            if not user_id:
                continue
            _expire_trial(pool, user_id, dry_run=False)
            updated += 1

        print(f"apply: обновлено {updated} строк premium_trials. Строки не удалялись.")
        return 0
    finally:
        if pool is not None:
            pool.stop()
        driver.stop()


if __name__ == "__main__":
    sys.exit(main())