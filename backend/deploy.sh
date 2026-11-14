#!/usr/bin/env bash
set -Eeuo pipefail

CLOUD_ID="b1g4vj1h1a2ebmrl0geu"
FOLDER_ID="b1gf2t5b7torui7nm4be"
SERVICE_ACCOUNT_NAME="put-k-sebe-backend"
GATEWAY_SA_NAME="put-k-sebe-gateway"
DB_NAME="put-k-sebe-db"
SECRET_NAME="put-k-sebe-telegram-bot"
ADMIN_SECRET_NAME="put-k-sebe-admin-password"
TRIBUTE_SECRET_NAME="put-k-sebe-tribute-api-key"
DEEPSEEK_SECRET_NAME="put-k-sebe-deepseek-api-key"
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
# Prefer the service account already attached to the live function.
# This avoids assuming a service-account name that may differ in the existing cloud.
EXISTING_FUNCTION_JSON="$(yc serverless function get "$FUNCTION_NAME" --format json 2>/dev/null || true)"
EXISTING_SA_ID="$(printf '%s' "$EXISTING_FUNCTION_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin) if sys.stdin.readable() else {}; print(d.get("service_account_id",""))' 2>/dev/null || true)"
if [[ -n "$EXISTING_SA_ID" ]]; then
  SA_JSON="$(yc iam service-account get "$EXISTING_SA_ID" --format json)" || fail "Не найден сервисный аккаунт текущей Cloud Function: $EXISTING_SA_ID"
  SA_ID="$(printf '%s' "$SA_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  SA_FOLDER_ID="$(printf '%s' "$SA_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("folder_id",""))')"
  [[ "$SA_FOLDER_ID" == "$FOLDER_ID" ]] || fail "Сервисный аккаунт текущей функции находится в другой папке: $SA_FOLDER_ID"
else
  SA_JSON="$(yc iam service-account get "$SERVICE_ACCOUNT_NAME" --format json 2>/dev/null || true)"
  [[ -n "$SA_JSON" ]] || fail "Не найден сервисный аккаунт $SERVICE_ACCOUNT_NAME и у текущей Cloud Function нет привязанного service_account_id"
  SA_ID="$(printf '%s' "$SA_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
fi

GATEWAY_SA_JSON="$(yc iam service-account get "$GATEWAY_SA_NAME" --format json 2>/dev/null || true)"
if [[ -z "$GATEWAY_SA_JSON" ]]; then
  GATEWAY_SA_JSON="$(yc iam service-account create --name "$GATEWAY_SA_NAME" --description "API Gateway invoker for Put-k-sebe" --format json)" || fail "Не удалось создать сервисный аккаунт API Gateway"
fi
GATEWAY_SA_ID="$(printf '%s' "$GATEWAY_SA_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

DB_JSON="$(yc ydb database get "$DB_NAME" --format json)" || fail "Не найдена база $DB_NAME"
DB_STATUS="$(printf '%s' "$DB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
[[ "$DB_STATUS" == "RUNNING" ]] || fail "YDB находится в статусе $DB_STATUS, а не RUNNING"
DB_ID="$(printf '%s' "$DB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
readarray -t DB_CFG < <(printf '%s' "$DB_JSON" | python3 -c 'import json,sys; u=json.load(sys.stdin)["endpoint"]; a,b=u.split("/?database=",1); print(a.rstrip("/")); print("/"+b.lstrip("/"))')
YDB_ENDPOINT="${DB_CFG[0]}"
YDB_DATABASE="${DB_CFG[1]}"

# The function creates tables and performs read/write queries. Grant only the
# database-scoped YDB editor role required for those operations.
DB_BINDINGS="$(yc ydb database list-access-bindings --id "$DB_ID" --format json)" || fail "Не удалось прочитать права доступа YDB"
HAS_YDB_EDITOR="$(printf '%s' "$DB_BINDINGS" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("access_bindings",d.get("accessBindings",[])); print("yes" if any(x.get("role_id",x.get("roleId"))=="ydb.editor" and (x.get("subject") or {}).get("id")==sys.argv[1] for x in a) else "no")' "$SA_ID")"
if [[ "$HAS_YDB_EDITOR" != "yes" ]]; then
  yc ydb database add-access-binding --id "$DB_ID" \
    --role ydb.editor --service-account-id "$SA_ID" >/dev/null || fail "Не удалось выдать backend-сервисному аккаунту ydb.editor на базу"
fi

# Re-read bindings after the grant. Do not continue with a deployment that
# merely assumed the permission was applied.
DB_BINDINGS="$(yc ydb database list-access-bindings --id "$DB_ID" --format json)" || fail "Не удалось повторно проверить права доступа YDB"
HAS_YDB_EDITOR="$(printf '%s' "$DB_BINDINGS" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("access_bindings",d.get("accessBindings",[])); print("yes" if any(x.get("role_id",x.get("roleId"))=="ydb.editor" and (x.get("subject") or {}).get("id")==sys.argv[1] for x in a) else "no")' "$SA_ID")"
[[ "$HAS_YDB_EDITOR" == "yes" ]] || fail "У backend service account $SA_ID отсутствует подтверждённая роль ydb.editor на базе $DB_NAME"

echo "    Backend SA: $SA_ID"
echo "    Gateway SA: $GATEWAY_SA_ID"
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
SECRETS_JSON="$(yc lockbox secret list --format json)" || fail "Не удалось прочитать список Lockbox secrets"
SECRET_ID="$(printf '%s' "$SECRETS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("secrets",[]); print(next((x["id"] for x in a if x.get("name")=="put-k-sebe-telegram-bot"), ""))')"
if [[ -z "$SECRET_ID" ]]; then
  read -rsp "Вставь токен Telegram-бота (ввод скрыт): " BOT_TOKEN
  echo
  [[ -n "$BOT_TOKEN" ]] || fail "Токен не может быть пустым"
  CREATED_SECRET="$(printf '%s' "$BOT_TOKEN" | python3 -c 'import json,sys; print(json.dumps([{"key":"bot_token","text_value":sys.stdin.read()}]))' | yc lockbox secret create --name "$SECRET_NAME" --description "Telegram bot token for Put-k-sebe backend" --payload - --format json)" || fail "Не удалось создать секрет в Lockbox"
  unset BOT_TOKEN
  SECRET_ID="$(printf '%s' "$CREATED_SECRET" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
fi
SECRET_INFO="$(yc lockbox secret get "$SECRET_ID" --format json)"
VERSION_ID="$(printf '%s' "$SECRET_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')"
if ! yc lockbox secret list-access-bindings "$SECRET_ID" 2>/dev/null | grep -q "$SA_ID"; then
  yc lockbox secret add-access-binding --id "$SECRET_ID" --role lockbox.payloadViewer --service-account-id "$SA_ID"
fi
echo "    Lockbox secret готов."

echo "Подготавливаю пароль админ-панели в Lockbox..."
ADMIN_SECRETS_JSON="$(yc lockbox secret list --format json)" || fail "Не удалось прочитать список Lockbox secrets"
ADMIN_SECRET_ID="$(printf '%s' "$ADMIN_SECRETS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("secrets",[]); print(next((x["id"] for x in a if x.get("name")=="put-k-sebe-admin-password"), ""))')"
if [[ -z "$ADMIN_SECRET_ID" ]]; then
  read -rsp "Придумай пароль админ-панели (не используй пароль от других сервисов): " ADMIN_PASSWORD
  echo
  [[ ${#ADMIN_PASSWORD} -ge 16 ]] || fail "Пароль должен содержать не менее 16 символов"
  CREATED_ADMIN_SECRET="$(printf '%s' "$ADMIN_PASSWORD" | python3 -c 'import json,sys; print(json.dumps([{"key":"admin_password","text_value":sys.stdin.read()}]))' | yc lockbox secret create --name "$ADMIN_SECRET_NAME" --description "Admin dashboard password for Put-k-sebe" --payload - --format json)" || fail "Не удалось создать пароль в Lockbox"
  unset ADMIN_PASSWORD
  ADMIN_SECRET_ID="$(printf '%s' "$CREATED_ADMIN_SECRET" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
fi
ADMIN_SECRET_INFO="$(yc lockbox secret get "$ADMIN_SECRET_ID" --format json)"
ADMIN_VERSION_ID="$(printf '%s' "$ADMIN_SECRET_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')"
if ! yc lockbox secret list-access-bindings "$ADMIN_SECRET_ID" 2>/dev/null | grep -q "$SA_ID"; then
  yc lockbox secret add-access-binding --id "$ADMIN_SECRET_ID" --role lockbox.payloadViewer --service-account-id "$SA_ID"
fi
echo "    Пароль админ-панели хранится в Lockbox."

echo "Подключаю API-ключ Tribute из Lockbox..."
TRIBUTE_SECRET_ID="$(printf '%s' "$ADMIN_SECRETS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("secrets",[]); print(next((x["id"] for x in a if x.get("name")=="put-k-sebe-tribute-api-key"), ""))')"
[[ -n "$TRIBUTE_SECRET_ID" ]] || fail "Не найден Lockbox secret $TRIBUTE_SECRET_NAME"
TRIBUTE_SECRET_INFO="$(yc lockbox secret get "$TRIBUTE_SECRET_ID" --format json)"
TRIBUTE_VERSION_ID="$(printf '%s' "$TRIBUTE_SECRET_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')"
if ! yc lockbox secret list-access-bindings "$TRIBUTE_SECRET_ID" 2>/dev/null | grep -q "$SA_ID"; then
  yc lockbox secret add-access-binding --id "$TRIBUTE_SECRET_ID" --role lockbox.payloadViewer --service-account-id "$SA_ID"
fi
echo "    API-ключ Tribute подключён через Lockbox."

echo "Подключаю API-ключ DeepSeek из Lockbox..."
DEEPSEEK_SECRETS_JSON="$(yc lockbox secret list --format json)" || fail "Не удалось прочитать список Lockbox secrets"
DEEPSEEK_SECRET_ID="$(printf '%s' "$DEEPSEEK_SECRETS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("secrets",[]); print(next((x["id"] for x in a if x.get("name")=="put-k-sebe-deepseek-api-key"), ""))')"
[[ -n "$DEEPSEEK_SECRET_ID" ]] || fail "Не найден Lockbox secret $DEEPSEEK_SECRET_NAME. Сначала создай его."
DEEPSEEK_SECRET_INFO="$(yc lockbox secret get "$DEEPSEEK_SECRET_ID" --format json)"
DEEPSEEK_VERSION_ID="$(printf '%s' "$DEEPSEEK_SECRET_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')"
if ! yc lockbox secret list-access-bindings "$DEEPSEEK_SECRET_ID" 2>/dev/null | grep -q "$SA_ID"; then
  yc lockbox secret add-access-binding --id "$DEEPSEEK_SECRET_ID" --role lockbox.payloadViewer --service-account-id "$SA_ID"
fi
echo "    API-ключ DeepSeek подключён через Lockbox."

echo "[5/8] Создаю приватный бакет для ZIP-файла функции..."
BUCKET_NAME="put-k-sebe-fn-${CLOUD_ID:0:20}"
OBJECT_KEY="functions/put-k-sebe-api.zip"
BUCKET_JSON="$(yc storage bucket get "$BUCKET_NAME" --format json 2>/dev/null || true)"
if [[ -z "$BUCKET_JSON" ]]; then
  yc storage bucket create --name "$BUCKET_NAME" --max-size 104857600 >/dev/null || fail "Не удалось создать приватный бакет $BUCKET_NAME"
fi
FOLDER_BINDINGS="$(yc resource-manager folder list-access-bindings "$FOLDER_ID" --format json)" || fail "Не удалось прочитать права папки"
HAS_STORAGE_VIEWER="$(printf '%s' "$FOLDER_BINDINGS" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("access_bindings",[]); print("yes" if any(x.get("role_id",x.get("roleId"))=="storage.viewer" and (x.get("subject") or {}).get("id")==sys.argv[1] for x in a) else "no")' "$SA_ID")"
if [[ "$HAS_STORAGE_VIEWER" != "yes" ]]; then
  yc resource-manager folder add-access-binding "$FOLDER_ID" \
    --role storage.viewer \
    --subject "serviceAccount:$SA_ID" >/dev/null || fail "Не удалось выдать сервисному аккаунту storage.viewer"
fi
yc storage s3api put-object --body "$ZIP_FILE" --bucket "$BUCKET_NAME" --key "$OBJECT_KEY" >/dev/null || fail "Не удалось загрузить ZIP-файл в Object Storage"
echo "    Архив загружен в закрытый бакет: $BUCKET_NAME"

echo "[6/8] Создаю или обновляю Cloud Function..."
FUNCTION_JSON="$(yc serverless function get "$FUNCTION_NAME" --format json 2>/dev/null || true)"
if [[ -z "$FUNCTION_JSON" ]]; then
  FUNCTION_JSON="$(yc serverless function create "$FUNCTION_NAME" --description "API for Put-k-sebe Telegram Mini App" --format json)"
fi
FUNCTION_ID="$(printf '%s' "$FUNCTION_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

if ! yc serverless function list-access-bindings "$FUNCTION_ID" 2>/dev/null | grep -q "$GATEWAY_SA_ID"; then
  yc serverless function add-access-binding --id "$FUNCTION_ID" --role functions.functionInvoker --subject "serviceAccount:$GATEWAY_SA_ID" || fail "Не удалось разрешить API Gateway вызывать Cloud Function"
fi
# Timer invocation permission belongs on the folder, not on the individual function.
# Keep this binding: removing it makes the timer's service account unable to invoke the function.
TIMER_BINDINGS="$(yc resource-manager folder list-access-bindings "$FOLDER_ID" --format json)" || fail "Не удалось прочитать IAM-привязки каталога для таймера"
HAS_TIMER_INVOKER="$(printf '%s' "$TIMER_BINDINGS" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("access_bindings",[]); print("yes" if any(x.get("role_id",x.get("roleId"))=="functions.functionInvoker" and (x.get("subject") or {}).get("id")==sys.argv[1] for x in a) else "no")' "$SA_ID")"
if [[ "$HAS_TIMER_INVOKER" != "yes" ]]; then
  yc resource-manager folder add-access-binding "$FOLDER_ID" \
    --role functions.functionInvoker \
    --subject "serviceAccount:$SA_ID" >/dev/null || fail "Не удалось выдать backend-сервисному аккаунту functions.functionInvoker на каталог"
fi

yc serverless function version create \
  --function-id "$FUNCTION_ID" \
  --runtime python312 \
  --entrypoint index.handler \
  --memory 512MB \
  --execution-timeout 60s \
  --package-bucket-name "$BUCKET_NAME" \
  --package-object-name "$OBJECT_KEY" \
  --service-account-id "$SA_ID" \
  --environment "YDB_ENDPOINT=$YDB_ENDPOINT,YDB_DATABASE=$YDB_DATABASE" \
  --secret "environment-variable=BOT_TOKEN,id=$SECRET_ID,version-id=$VERSION_ID,key=bot_token" \
  --secret "environment-variable=ADMIN_PASSWORD,id=$ADMIN_SECRET_ID,version-id=$ADMIN_VERSION_ID,key=admin_password" \
  --secret "environment-variable=TRIBUTE_API_KEY,id=$TRIBUTE_SECRET_ID,version-id=$TRIBUTE_VERSION_ID,key=api_key" \
  --secret "environment-variable=DEEPSEEK_API_KEY,id=$DEEPSEEK_SECRET_ID,version-id=$DEEPSEEK_VERSION_ID,key=api_key" \
  --description "Put-k-sebe API deployment" >/dev/null

# Timer runs each minute; backend generates summaries only for users at local 23:59.
# Use Trigger API v2 (the v1 CLI is deprecated).
TIMER_NAME="put-k-sebe-daily-summary"
TIMER_JSON="$(yc serverless trigger v2 get "$TIMER_NAME" --format json 2>/dev/null || true)"
if [[ -z "$TIMER_JSON" ]]; then
  TIMER_ACTION_FILE="$WORKDIR/timer-action.json"
  FUNCTION_TAG='$latest'
  cat > "$TIMER_ACTION_FILE" <<JSON
{"invoke_function":{"function_id":"$FUNCTION_ID","function_tag":"$FUNCTION_TAG","service_account_id":"$SA_ID"}}
JSON
  yc serverless trigger v2 create timer "$TIMER_NAME" \
    --description "One daily summary at 23:59 in each user's local timezone" \
    --cron-expression '* * * * ? *' \
    --payload '{"job":"daily_summary"}' \
    --action "@$TIMER_ACTION_FILE" >/dev/null || fail "Не удалось создать таймер ежедневной сводки"
else
  echo "    Таймер ежедневной сводки уже существует."
fi

# Cloud Functions copies the package during version creation; remove the temporary object.
yc storage s3api delete-object --bucket "$BUCKET_NAME" --key "$OBJECT_KEY" >/dev/null 2>&1 || true

echo "[7/8] Готовлю спецификацию API Gateway..."
SPEC_FILE="$WORKDIR/backend/api-gateway.generated.yaml"
cat > "$SPEC_FILE" <<YAML
openapi: "3.0.0"
info:
  title: Put-k-sebe API
  version: "1.0.0"
x-yc-apigateway:
  cors:
    origin: '*'
    methods: [GET, PUT, DELETE, POST, OPTIONS]
    allowedHeaders: [Content-Type, X-Telegram-Init-Data, X-User-Timezone, X-Admin-Password, trbt-signature]
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
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /health/db:
    get:
      operationId: healthDatabase
      responses:
        "200":
          description: YDB connectivity check passed
        "503":
          description: YDB connectivity check failed
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /health/schema:
    get:
      operationId: healthSchema
      responses:
        "200":
          description: YDB schema initialization passed
        "503":
          description: YDB schema initialization failed
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/webhooks/tribute:
    post:
      operationId: tributeWebhook
      responses:
        "200":
          description: Tribute event processed
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/profile:
    post:
      operationId: registerUserProfile
      responses:
        "200":
          description: User profile updated
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/consents:
    get:
      operationId: getUserConsents
      responses:
        "200":
          description: User AI consent settings
        "401":
          description: Unauthorized
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
    put:
      operationId: saveUserConsents
      responses:
        "200":
          description: Consent settings saved
        "400":
          description: Invalid consent values
        "401":
          description: Unauthorized
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/premium:
    get:
      operationId: getPremiumStatus
      responses:
        "200":
          description: Premium entitlement status
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/ai/test:
    post:
      operationId: openaiConnectionTest
      responses:
        "200":
          description: DeepSeek connection test
        "403":
          description: Premium required
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/ai/analyze-onboarding:
    post:
      operationId: analyzeOnboarding
      responses:
        "200":
          description: Personal pattern hypotheses from onboarding
        "502":
          description: AI provider error
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/ai/analyze-entry:
    post:
      operationId: analyzeDiaryEntry
      responses:
        "200":
          description: Diary entry analysis
        "403":
          description: Premium required
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/ai/daily-summary:
    post:
      operationId: createDailySummary
      responses:
        "200":
          description: Daily reflection summary
        "403":
          description: Premium required
        "429":
          description: Daily quota reached
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/ai/usage:
    get:
      operationId: getDailyAiUsage
      responses:
        "200":
          description: Daily AI usage and remaining quota
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/trial:
    post:
      operationId: startPremiumTrial
      responses:
        "200":
          description: Trial started
        "409":
          description: Trial already used
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/events:
    post:
      operationId: postAnalyticsEvent
      responses:
        "202":
          description: Event accepted
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/admin/users:
    get:
      operationId: getAdminUsers
      responses:
        "200":
          description: Admin user list
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/admin/user:
    get:
      operationId: getAdminUser
      responses:
        "200":
          description: Admin user details
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
  /v1/admin/analytics:
    get:
      operationId: getAdminAnalytics
      responses:
        "200":
          description: Aggregate product analytics
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
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
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
    put:
      operationId: saveState
      responses:
        "200":
          description: Save user state
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
    delete:
      operationId: deleteState
      responses:
        "200":
          description: Delete user state
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: "$FUNCTION_ID"
        service_account_id: "$GATEWAY_SA_ID"
        payload_format_version: "1.0"
YAML

echo "[8/8] Создаю или обновляю API Gateway..."
GATEWAY_JSON="$(yc serverless api-gateway get "$GATEWAY_NAME" --format json 2>/dev/null || true)"
if [[ -z "$GATEWAY_JSON" ]]; then
  GATEWAY_JSON="$(yc serverless api-gateway create "$GATEWAY_NAME" --description "API for Put-k-sebe Telegram Mini App" --spec "$SPEC_FILE" --format json)"
else
  yc serverless api-gateway update "$GATEWAY_NAME" --spec "$SPEC_FILE" >/dev/null
  GATEWAY_JSON="$(yc serverless api-gateway get "$GATEWAY_NAME" --format json)"
fi
GATEWAY_ID="$(printf '%s' "$GATEWAY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
GATEWAY_JSON="$(yc serverless api-gateway get "$GATEWAY_ID" --format json)"
GATEWAY_DOMAIN="$(printf '%s' "$GATEWAY_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("domain") or d.get("domain_name") or d.get("domainName") or "")')"
[[ -n "$GATEWAY_DOMAIN" ]] || fail "API Gateway создан, но поле domain не найдено. ID: $GATEWAY_ID"

echo "[9/9] Проверяю API, YDB и схему базы..."
API_URL="https://$GATEWAY_DOMAIN"
check_endpoint() {
  local endpoint="$1"
  local label="$2"
  local ok="false"
  local output="/tmp/put-k-sebe-health.json"
  for attempt in 1 2 3 4 5 6; do
    if curl -fsS "$API_URL$endpoint" -o "$output"; then
      if python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); raise SystemExit(0 if d.get("ok") is True else 1)' "$output"; then
        ok="true"
        break
      fi
    fi
    sleep 5
  done
  if [[ "$ok" != "true" ]]; then
    echo "Проверка $label не пройдена ($endpoint):"
    cat "$output" 2>/dev/null || true
    echo
    fail "API не считается развёрнутым: $label недоступен. Смотри Cloud Function logs."
  fi
  echo "    $label: OK"
}
check_endpoint "/health" "Backend + YDB + schema"
check_endpoint "/health/db" "YDB connection"
check_endpoint "/health/schema" "YDB schema"
echo "=============================================="
echo "РАЗВЁРТЫВАНИЕ BACKEND ЗАВЕРШЕНО"
echo "API_BASE=$API_URL"
echo "TRIBUTE_WEBHOOK_URL=$API_URL/v1/webhooks/tribute"
echo "PREMIUM_STATUS_URL=$API_URL/v1/premium"
echo "GATEWAY_ID=$GATEWAY_ID"
echo "FUNCTION_ID=$FUNCTION_ID"
echo "=============================================="
echo "Скопируй сюда только строки API_BASE и TRIBUTE_WEBHOOK_URL. Секреты не присылай."
echo "После проверки подключим статус Premium в frontend."
