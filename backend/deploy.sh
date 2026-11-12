#!/usr/bin/env bash
set -Eeuo pipefail

CLOUD_ID="b1g4vj1h1a2ebmrl0geu"
FOLDER_ID="b1gf2t5b7torui7nm4be"
SERVICE_ACCOUNT_NAME="put-k-sebe-backend"
DB_NAME="put-k-sebe-db"
SECRET_NAME="put-k-sebe-telegram-bot"
FUNCTION_NAME="put-k-sebe-api"
GATEWAY_NAME="put-k-sebe-gateway"
REPO_URL="https://github.com/oleggrigorev131313/put-k-sebe.git"
WORKDIR="$(mktemp -d /tmp/put-k-sebe.XXXXXX)"
BUILD_DIR="$WORKDIR/.build"
ZIP_FILE="$WORKDIR/backend.zip"

fail() { echo; echo "ОШИБКА: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Не найдена команда: $1"; }

need yc
need tar
need python3
need curl

echo "== Путь к себе: автоматическое развёртывание =="
echo "Будут использованы существующие облако, папка и YDB."
echo "Новая база данных создаваться не будет."
echo "Будут созданы Lockbox secret, Cloud Function и API Gateway."
echo "Серверные вызовы тарифицируются по действующим тарифам Yandex Cloud."
echo
read -r -p "Продолжить развёртывание? [y/N] " CONFIRM
[[ "$CONFIRM" == "y" || "$CONFIRM" == "Y" ]] || { echo "Отменено, ресурсы не изменены."; exit 0; }
echo

CURRENT_CLOUD="$(yc config get cloud-id 2>/dev/null || true)"
CURRENT_FOLDER="$(yc config get folder-id 2>/dev/null || true)"
if [[ "$CURRENT_CLOUD" != "$CLOUD_ID" ]]; then yc config set cloud-id "$CLOUD_ID"; fi
if [[ "$CURRENT_FOLDER" != "$FOLDER_ID" ]]; then yc config set folder-id "$FOLDER_ID"; fi

echo "[1/8] Получаю сервисный аккаунт и базу данных..."
SA_JSON="$(yc iam service-account get "$SERVICE_ACCOUNT_NAME" --format json)" || fail "Не найден сервисный аккаунт $SERVICE_ACCOUNT_NAME"
SA_ID="$(printf '%s' "$SA_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

DB_JSON="$(yc ydb database get "$DB_NAME" --format json)" || fail "Не найдена база $DB_NAME"
DB_STATUS="$(printf '%s' "$DB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
[[ "$DB_STATUS" == "RUNNING" ]] || fail "YDB находится в статусе $DB_STATUS, а не RUNNING"
readarray -t DB_CFG < <(printf '%s' "$DB_JSON" | python3 -c 'import json,sys; u=json.load(sys.stdin)["endpoint"]; a,b=u.split("/?database=",1); print(a.rstrip("/")); print("/"+b.lstrip("/"))')
YDB_ENDPOINT="${DB_CFG[0]}"
YDB_DATABASE="${DB_CFG[1]}"

echo "    Сервисный аккаунт: $SA_ID"
echo "    База: $DB_NAME ($DB_STATUS)"

echo "[2/8] Скачиваю исходники из GitHub (без git clone)..."
ARCHIVE_FILE="$WORKDIR/repository.tar.gz"
ARCHIVE_URL="https://codeload.github.com/oleggrigorev131313/put-k-sebe/tar.gz/refs/heads/main"
curl -fLsS --retry 3 --connect-timeout 15 "$ARCHIVE_URL" -o "$ARCHIVE_FILE" || fail "Не удалось скачать архив репозитория"
tar -xzf "$ARCHIVE_FILE" --strip-components=1 -C "$WORKDIR" || fail "Не удалось распаковать архив репозитория"
rm -f "$ARCHIVE_FILE"
[[ -f "$WORKDIR/backend/index.py" ]] || fail "В репозитории нет backend/index.py"
[[ -f "$WORKDIR/backend/requirements.txt" ]] || fail "Нет backend/requirements.txt"

echo "[3/8] Собираю пакет Cloud Function (Python 3.12)..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
python3 -m pip install --disable-pip-version-check --quiet \
  --target "$BUILD_DIR" \
  --python-version 3.12 --implementation cp --abi cp312 \
  --platform manylinux2014_x86_64 --only-binary=:all: \
  -r "$WORKDIR/backend/requirements.txt" || fail "Не удалось установить зависимости Python"
cp "$WORKDIR/backend/index.py" "$BUILD_DIR/index.py"
python3 - "$BUILD_DIR" "$ZIP_FILE" <<'PY'
import os, sys, zipfile
root, target = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(base, name)
            z.write(full, os.path.relpath(full, root))
print("Пакет собран:", os.path.getsize(target), "байт")
PY

echo "[4/8] Подготавливаю токен бота в Lockbox..."
SECRET_JSON="$(yc lockbox secret get "$SECRET_NAME" --format json 2>/dev/null || true)"
if [[ -z "$SECRET_JSON" ]]; then
  read -rsp "Вставь токен Telegram-бота (ввод скрыт): " BOT_TOKEN
  echo
  [[ -n "$BOT_TOKEN" ]] || fail "Токен не может быть пустым"
  SECRET_JSON="$(printf '%s' "$BOT_TOKEN" | python3 -c 'import json,sys; print(json.dumps([{"key":"bot_token","text_value":sys.stdin.read()}]))' | yc lockbox secret create --name "$SECRET_NAME" --description "Telegram bot token for Put-k-sebe backend" --payload - --format json)" || fail "Не удалось создать секрет в Lockbox"
  unset BOT_TOKEN
fi
SECRET_ID="$(printf '%s' "$SECRET_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
SECRET_INFO="$(yc lockbox secret get "$SECRET_ID" --format json)"
VERSION_ID="$(printf '%s' "$SECRET_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')"
if ! yc lockbox secret list-access-bindings "$SECRET_ID" 2>/dev/null | grep -q "$SA_ID"; then
  yc lockbox secret add-access-binding --id "$SECRET_ID" --role lockbox.payloadViewer --service-account-id "$SA_ID"
fi
echo "    Lockbox secret готов."

echo "[5/8] Создаю или обновляю Cloud Function..."
FUNCTION_JSON="$(yc serverless function get "$FUNCTION_NAME" --format json 2>/dev/null || true)"
if [[ -z "$FUNCTION_JSON" ]]; then
  FUNCTION_JSON="$(yc serverless function create --name "$FUNCTION_NAME" --description "API for Put-k-sebe Telegram Mini App" --format json)"
fi
FUNCTION_ID="$(printf '%s' "$FUNCTION_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

if ! yc serverless function list-access-bindings "$FUNCTION_ID" 2>/dev/null | grep -q "$SA_ID"; then
  yc serverless function add-access-binding --id "$FUNCTION_ID" --role functions.functionInvoker --subject "serviceAccount:$SA_ID"
fi

yc serverless function version create \
  --function-id "$FUNCTION_ID" \
  --runtime python312 \
  --entrypoint index.handler \
  --memory 512m \
  --execution-timeout 30s \
  --source-path "$ZIP_FILE" \
  --service-account-id "$SA_ID" \
  --environment "YDB_ENDPOINT=$YDB_ENDPOINT,YDB_DATABASE=$YDB_DATABASE" \
  --secret "environment-variable=BOT_TOKEN,id=$SECRET_ID,version-id=$VERSION_ID,key=bot_token" \
  --description "Put-k-sebe API deployment" >/dev/null

echo "[6/8] Готовлю спецификацию API Gateway..."
SPEC_FILE="$WORKDIR/backend/api-gateway.generated.yaml"
cat > "$SPEC_FILE" <<YAML
openapi: "3.0.0"
info:
  title: Put-k-sebe API
  version: "1.0.0"
x-yc-apigateway:
  cors:
    origin: true
    methods: [GET, PUT, DELETE, OPTIONS]
    allowedHeaders: [Content-Type, X-Telegram-Init-Data]
    maxAge: 3600
paths:
  /health:
    get:
      operationId: health
      responses:
        "200":
          description: Health status
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$SA_ID"
        payload_format_version: "1.0"
  /v1/state:
    get:
      operationId: getState
      responses:
        "200":
          description: User state
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$SA_ID"
        payload_format_version: "1.0"
    put:
      operationId: saveState
      responses:
        "200":
          description: Save user state
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$SA_ID"
        payload_format_version: "1.0"
    delete:
      operationId: deleteState
      responses:
        "200":
          description: Delete user state
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$SA_ID"
        payload_format_version: "1.0"
YAML

echo "[7/8] Создаю или обновляю API Gateway..."
GATEWAY_JSON="$(yc serverless api-gateway get "$GATEWAY_NAME" --format json 2>/dev/null || true)"
if [[ -z "$GATEWAY_JSON" ]]; then
  GATEWAY_JSON="$(yc serverless api-gateway create --name "$GATEWAY_NAME" --description "API for Put-k-sebe Telegram Mini App" --spec "$SPEC_FILE" --format json)"
else
  yc serverless api-gateway update "$GATEWAY_NAME" --spec "$SPEC_FILE" >/dev/null
  GATEWAY_JSON="$(yc serverless api-gateway get "$GATEWAY_NAME" --format json)"
fi
GATEWAY_ID="$(printf '%s' "$GATEWAY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
GATEWAY_JSON="$(yc serverless api-gateway get "$GATEWAY_ID" --format json)"
GATEWAY_DOMAIN="$(printf '%s' "$GATEWAY_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("domain") or d.get("domain_name") or d.get("domainName") or "")')"
[[ -n "$GATEWAY_DOMAIN" ]] || fail "API Gateway создан, но поле domain не найдено. ID: $GATEWAY_ID"

echo "[8/8] Проверяю API..."
API_URL="https://$GATEWAY_DOMAIN"
for attempt in 1 2 3 4 5 6; do
  if curl -fsS "$API_URL/health" >/tmp/put-k-sebe-health.json; then break; fi
  sleep 5
done
cat /tmp/put-k-sebe-health.json 2>/dev/null || true
echo
echo "=============================================="
echo "РАЗВЁРТЫВАНИЕ BACKEND ЗАВЕРШЕНО"
echo "API_BASE=$API_URL"
echo "GATEWAY_ID=$GATEWAY_ID"
echo "FUNCTION_ID=$FUNCTION_ID"
echo "=============================================="
echo "Скопируй сюда только строку API_BASE. Токен бота и содержимое Lockbox не присылай."
echo "Следующим шагом я подключу URL к frontend, добавлю облачную синхронизацию и onboarding."
