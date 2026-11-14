# Путь к себе — backend

Backend для Telegram Mini App на Yandex Cloud: API Gateway → Cloud Functions (Python 3.12) → YDB Serverless.

## Что входит

- Серверная проверка подписи Telegram Mini App initData. Клиентские данные initDataUnsafe не используются для авторизации.
- Облачное сохранение состояния пользователя (дневник, паттерны, выполненные практики, onboarding).
- Обязательная изоляция данных по Telegram user ID.
- Lockbox для токена бота; токен не попадает в GitHub и браузер.
- Отдельная таблица событий использования: Telegram ID хранится в HMAC-псевдонимизированном виде, а тексты дневников в события не попадают.
- Таблица user_profiles хранит Telegram ID, имя/username, часовой пояс и временные метки для пользовательской аналитики; профиль регистрируется только после принятия настроек конфиденциальности.
- Админ API: /v1/admin/analytics, /v1/admin/users и /v1/admin/user. Доступ закрыт отдельным паролем администратора.
- API: GET /health, GET/PUT/DELETE /v1/state, POST /v1/profile, POST /v1/events.

## Что пока не подключено

- AI-анализ дневниковых записей доступен на Premium; ежедневная сводка доступна на всех тарифах один раз в локальные сутки.
- Платежи, подписки и Tribute подключены отдельными маршрутами backend.
- Админ-панель защищена паролем и показывает профили и историю действий; содержимое AI-истории доступно только при отдельном согласии на её хранение.
- Завершённый юридический контур для обработки персональных данных. До публичного запуска нужны тексты согласия, политика конфиденциальности, сроки хранения и порядок удаления, проверенные с юристом по применимому законодательству.

## Развёртывание

Скрипт не создаёт новую базу и не удаляет данные. Он обновляет существующие Cloud Function и API Gateway, при необходимости выдаёт уже используемые роли и создаёт **только отсутствующие** таблицы YDB.

Из каталога репозитория или из Cloud Shell после скачивания скрипта:

    bash backend/deploy.sh --yes --ref main

До слияния ветки укажите её имя в `--ref`, иначе скрипт скачает `main`. Неинтерактивный режим: `--yes` или `PUT_K_SEBE_YES=1`. Пропустить миграцию: `--skip-migrate`.

`deploy.sh` сверяет сервисный аккаунт `put-k-sebe-gateway` по имени через `yc`. Если id в облаке не совпадает с литералом в скрипте, деплой останавливается до изменений.

Успех деплоя — это `GET /health/schema` с data-plane чтением всех 9 таблиц, включая `user_consents` и `user_state`. `SELECT 1` на `/health` для этого не используется. Если проверка не прошла, скрипт печатает команду отката и завершается с ошибкой. Новая версия к этому моменту уже может быть `$latest`.

## Миграция схемы

`backend/migrate.py` сначала описывает существующие таблицы и создаёт только отсутствующие, по одной, с паузой и повтором при `400080`. Существующие таблицы и строки не изменяются.

Токен берётся из `YC_IAM_TOKEN` или `IAM_TOKEN`, иначе из `yc iam create-token`. Endpoint и путь базы — из `yc ydb database get put-k-sebe-db` (поле `endpoint`, часть до `/?database=` и путь после него).

```bash
python3 -m pip install 'ydb>=3.20,<4'
export YDB_ENDPOINT='grpcs://...'
export YDB_DATABASE='/ru-central1/.../...'
python3 backend/migrate.py --dry-run
python3 backend/migrate.py --apply
```

## Проверка и откат

```bash
curl -sS "https://d5dhdhkofvnr2t1fnkag.7qsg961h.apigw.yandexcloud.net/health/schema"
```

В ответе `ok` должен быть `true`, а `checks.user_consents` и `checks.user_state` — `ok`. Если нет, верните предыдущую версию функции (id печатает `deploy.sh`):

```bash
yc serverless function version set-tag --id <PREVIOUS_VERSION_ID> --tag '$latest'
```

## Переменные функции

- YDB_ENDPOINT — endpoint без database path.
- YDB_DATABASE — полный путь базы из endpoint.
- BOT_TOKEN, ADMIN_PASSWORD, TRIBUTE_API_KEY, DEEPSEEK_API_KEY — значения из Lockbox, передаваемые Cloud Functions как secret. Обработчик запросов сам секреты Lockbox не читает и DDL не выполняет.
- TELEGRAM_WEBHOOK_SECRET — необязательный. Если переменная не задана, секрет webhook выводится из BOT_TOKEN (см. ниже). `deploy.sh` её не создаёт и не передаёт, новый Lockbox-секрет не нужен.

## Команда /start в чате бота

Обновления бота раньше не приходили в backend: BOT_TOKEN используется только для проверки initData Mini App. Обработчик добавлен в ту же Cloud Function `put-k-sebe-api`. `deploy.sh` добавляет путь существующего API Gateway, не создавая новую функцию или шлюз:

`POST /v1/webhooks/telegram`

На `/start` и `/start <payload>` функция не ходит в `api.telegram.org`. Ответ вебхука — это сам вызов метода: HTTP 200, `Content-Type: application/json`, тело `{"method":"sendPhoto","chat_id":...,"photo":"https://oleggrigorev131313.github.io/put-k-sebe/assets/bot/welcome.jpg","caption":...,"reply_markup":...}` с кнопкой «Открыть дневник». Шлюз с `payload_format_version: 1.0` отдаёт Telegram `statusCode`, заголовки и `body` функции как есть; `isBase64Encoded` остаётся `false`. Остальные апдейты отвечают `{"ok":true}` сразу. Запись в YDB не выполняется. Чужой запрос без секрета получает 401.

Проверка секрета: заголовок `X-Telegram-Bot-Api-Secret-Token`. Ожидаемое значение — переменная `TELEGRAM_WEBHOOK_SECRET`, если она задана. Иначе это hex HMAC-SHA256 от `BOT_TOKEN` с меткой `put-k-sebe-telegram-webhook-v1` (64 символа `0-9a-f`, формат Telegram `secret_token`).

Картинка публикуется GitHub Pages с ветки `main`. Сначала влейте PR, дождитесь Pages, затем разверните backend скриптом **этой** ревизии: спецификация шлюза вшита в `deploy.sh`, флаг `--ref` подменяет только код функции.

После вливания, в Cloud Shell. Спека шлюза вшита в запускаемый скрипт, поэтому скачайте `deploy.sh` уже из `main`:

```bash
curl -fsSL -o /tmp/put-k-sebe-deploy.sh \
  "https://raw.githubusercontent.com/oleggrigorev131313/put-k-sebe/main/backend/deploy.sh"
bash /tmp/put-k-sebe-deploy.sh --yes --ref main
```

Из уже обновлённого клона репозитория то же самое: `bash backend/deploy.sh --yes --ref main`.

До вливания запускайте скрипт из ветки PR и укажите ту же ветку в `--ref`. Иначе шлюз останется без пути `/v1/webhooks/telegram`.

Один раз после деплоя укажите webhook. Токен только в переменной окружения этой сессии. Не включайте трассировку шелла, не коммитьте токен и не присылайте его в чат.

```bash
export BOT_TOKEN='<BOT_TOKEN>'
python3 - <<'PY'
import hashlib, hmac, json, os, urllib.request
token = os.environ["BOT_TOKEN"]
secret = hmac.new(
    token.encode("utf-8"),
    b"put-k-sebe-telegram-webhook-v1",
    hashlib.sha256,
).hexdigest()
body = json.dumps({
    "url": "https://d5dhdhkofvnr2t1fnkag.7qsg961h.apigw.yandexcloud.net/v1/webhooks/telegram",
    "secret_token": secret,
    "allowed_updates": ["message"],
    "drop_pending_updates": False,
}).encode("utf-8")
request = urllib.request.Request(
    "https://api.telegram.org/bot" + token + "/setWebhook",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    print(response.read().decode("utf-8"))
PY
unset BOT_TOKEN
```

В ответе достаточно `{"ok":true,...}`. Проверка без секрета:

```bash
export BOT_TOKEN='<BOT_TOKEN>'
python3 - <<'PY'
import json, os, urllib.request
token = os.environ["BOT_TOKEN"]
request = urllib.request.Request("https://api.telegram.org/bot" + token + "/getWebhookInfo")
with urllib.request.urlopen(request, timeout=30) as response:
    info = json.loads(response.read().decode("utf-8"))
result = info.get("result") or {}
print(json.dumps({
    "ok": info.get("ok"),
    "url": result.get("url"),
    "pending_update_count": result.get("pending_update_count"),
    "last_error_message": result.get("last_error_message"),
}, ensure_ascii=False))
PY
unset BOT_TOKEN
```

`url` должен быть `https://d5dhdhkofvnr2t1fnkag.7qsg961h.apigw.yandexcloud.net/v1/webhooks/telegram`.

Локальная проверка сборки ответа без сети и без токена: `python3 backend/test_telegram_start.py`.

### Откат

Откат версии функции возвращает API, которым пользуется Mini App, к предыдущей версии. Путь `/v1/webhooks/telegram` в шлюзе при этом остаётся и получает 404 от старого кода, пока шлюз не обновлён скриптом без этого пути. Id печатает `deploy.sh` как `PREVIOUS_VERSION_ID`:

```bash
yc serverless function version set-tag --id <PREVIOUS_VERSION_ID> --tag '$latest'
```

Спека шлюза берётся из того `deploy.sh`, который вы запускаете, а не из `--ref`. Чтобы убрать путь, выполните скрипт коммита до этого изменения:

```bash
curl -fsSL -o /tmp/put-k-sebe-deploy-prev.sh \
  "https://raw.githubusercontent.com/oleggrigorev131313/put-k-sebe/<GIT_REF_BEFORE>/backend/deploy.sh"
bash /tmp/put-k-sebe-deploy-prev.sh --yes --ref <GIT_REF_BEFORE>
```

И снимите webhook, иначе Telegram будет повторять доставку:

```bash
export BOT_TOKEN='<BOT_TOKEN>'
python3 - <<'PY'
import os, urllib.request
token = os.environ["BOT_TOKEN"]
request = urllib.request.Request(
    "https://api.telegram.org/bot" + token + "/deleteWebhook",
    data=b"{}",
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    print(response.read().decode("utf-8"))
PY
unset BOT_TOKEN
```

Запросы считают, что таблицы уже есть. Если таблицы нет, ответ `503` со `stage` вида `table_missing:<имя>`. Таймер ежедневной сводки тоже не создаёт таблицы: раз в минуту читает только список часовых поясов и выходит, если ни у кого сейчас не 23:59.

## Модель хранения

user_state содержит один JSON-документ на Telegram user ID. Дневниковые записи не доступны администратору через API.
analytics_events содержит тип события, HMAC-псевдонимизированный Telegram ID, время и служебные метаданные; тексты дневников туда не записываются. user_profiles связывает реальный Telegram ID с профилем пользователя для закрытой админ-панели. user_state содержит дневник, паттерны и практики. ai_analyses хранит тексты AI-ответов только при включённом согласии ai_history_storage; при выключенном согласии записываются только минимальные метаданные использования. Администратор видит AI-ответы только при активном согласии на их хранение.

Сервис предназначен для саморефлексии и не является медицинской диагностикой или лечением.

## Сообщение бота об итогах дня

Таймер `put-k-sebe-daily-summary` уже вызывает эту же Cloud Function каждую минуту (cron `* * * * ? *`, payload `{"job":"daily_summary"}`). Функция сама выбирает часовые пояса, где сейчас 23:59, и работает при закрытом Mini App. Новый таймер, функция, шлюз и сервисный аккаунт не нужны. Вызов таймера идёт от существующего backend service account.

Часовой пояс уже пишется в `user_profiles.timezone` из `Intl.DateTimeFormat().resolvedOptions().timeZone` при регистрации профиля. Telegram user id хранится в `user_profiles.user_id` и для личного чата совпадает с chat id.

В 23:59 для пользователей с согласием на AI-обработку вызывается та же генерация сводки, что и из приложения. Если сводка есть (в том числе сделанная раньше в этот день), бот один раз шлёт текст «итоги готовы» и кнопку «Открыть дневник». Если за локальные сутки нет записей и практик, один раз уходит мягкое сообщение, что итоги не собраны. Повтор в те же сутки блокирует строка `daily_summary_claims` с ключом `notify:YYYY-MM-DD`. Это не новая таблица и не DDL. Ответ Telegram 403 (бот заблокирован) не повторяется в этот день. Отправка не чаще 20 сообщений в секунду. Исходящий вызов ждёт соединение 5 секунд и весь запрос 10 секунд. После трёх сетевых ошибок подряд этот запуск таймера больше ничего не отправляет, чтобы функция не висела до таймаута. Метка `notify:` при сетевой ошибке снимается, следующий запуск может попробовать снова.

Исходящие вызовы идут на `TELEGRAM_API_BASE` (по умолчанию `https://api.telegram.org`). Из этой облачной сети прямой доступ к Telegram не открывается, поэтому для ночных сообщений нужен HTTPS-прокси снаружи. Приветствие `/start` прокси не использует: его отправляет сам Telegram, прочитав тело вебхука. Прокси нужен только для `sendMessage` по таймеру.

Минимальный Cloudflare Worker. Не логируйте путь: в нём есть токен бота.

```javascript
export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target = "https://api.telegram.org" + incoming.pathname + incoming.search;
    const headers = new Headers(request.headers);
    headers.delete("host");
    return fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    });
  },
};
```

Подойдёт и любой другой HTTPS reverse proxy до `api.telegram.org`, если он отдаёт путь с корня хоста (`/bot<token>/sendMessage`).

Перед деплоем задайте адрес прокси в этой сессии и не печатайте его. `deploy.sh` добавит переменную в версию функции только если она задана, и сам её не выводит. Без переменной код снова зовёт `https://api.telegram.org`.

```bash
export TELEGRAM_API_BASE='https://<relay-host>'
bash backend/deploy.sh --yes --ref main
unset TELEGRAM_API_BASE
```

В Mini App нет отдельного адреса экрана итогов, поэтому кнопка открывает `https://oleggrigorev131313.github.io/put-k-sebe/`. Если сводка за сегодня уже есть, кнопка называется «Открыть итоги дня» и запрашивает сохранённый текст, а не запускает вторую генерацию. Повторно текст открывается при включённом хранении истории AI. Без этого согласия приложение по-прежнему не хранит текст сводки.

Проверка без сети и без токена: `python3 backend/test_daily_summary_notify.py`.

Деплой после вливания, в Cloud Shell, тем же скриптом:

```bash
bash backend/deploy.sh --yes --ref main
```

До вливания можно передать ветку в `--ref`: скрипт скачает `index.py` оттуда. Спека шлюза для этого изменения не нужна, таймер бьёт в функцию напрямую.

Таймер создавать второй раз не нужно. Если `yc serverless trigger v2 get put-k-sebe-daily-summary` пустой, та же команда, что в `deploy.sh` (каталог `b1gf2t5b7torui7nm4be`, функция `d4etj8737dl7lmfb36d0`, сервисный аккаунт backend `ajerjr8ohhto6gbm9gpo`):

```bash
cat > /tmp/put-k-sebe-timer-action.json <<'JSON'
{"invoke_function":{"function_id":"d4etj8737dl7lmfb36d0","function_tag":"$latest","service_account_id":"ajerjr8ohhto6gbm9gpo"}}
JSON
yc serverless trigger v2 create timer put-k-sebe-daily-summary \
  --description "One daily summary at 23:59 in each user's local timezone" \
  --cron-expression '* * * * ? *' \
  --payload '{"job":"daily_summary"}' \
  --action @/tmp/put-k-sebe-timer-action.json
```

Откат функции (id печатает `deploy.sh`):

```bash
yc serverless function version set-tag --id <PREVIOUS_VERSION_ID> --tag '$latest'
```

Таймер можно оставить: старая версия снова только готовит сводку и ничего не пишет в чат. Строки `notify:` в `daily_summary_claims` ни на что не влияют.
