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

Запросы считают, что таблицы уже есть. Если таблицы нет, ответ `503` со `stage` вида `table_missing:<имя>`. Таймер ежедневной сводки тоже не создаёт таблицы: раз в минуту читает только список часовых поясов и выходит, если ни у кого сейчас не 23:59.

## Модель хранения

user_state содержит один JSON-документ на Telegram user ID. Дневниковые записи не доступны администратору через API.
analytics_events содержит тип события, HMAC-псевдонимизированный Telegram ID, время и служебные метаданные; тексты дневников туда не записываются. user_profiles связывает реальный Telegram ID с профилем пользователя для закрытой админ-панели. user_state содержит дневник, паттерны и практики. ai_analyses хранит тексты AI-ответов только при включённом согласии ai_history_storage; при выключенном согласии записываются только минимальные метаданные использования. Администратор видит AI-ответы только при активном согласии на их хранение.

Сервис предназначен для саморефлексии и не является медицинской диагностикой или лечением.

## Сообщение бота об итогах дня

Таймер `put-k-sebe-daily-summary` уже вызывает эту же Cloud Function каждую минуту (cron `* * * * ? *`, payload `{"job":"daily_summary"}`). Функция сама выбирает часовые пояса, где сейчас 23:59, и работает при закрытом Mini App. Новый таймер, функция, шлюз и сервисный аккаунт не нужны. Вызов таймера идёт от существующего backend service account.

Часовой пояс уже пишется в `user_profiles.timezone` из `Intl.DateTimeFormat().resolvedOptions().timeZone` при регистрации профиля. Telegram user id хранится в `user_profiles.user_id` и для личного чата совпадает с chat id.

В 23:59 для пользователей с согласием на AI-обработку вызывается та же генерация сводки, что и из приложения. Если сводка есть (в том числе сделанная раньше в этот день), бот один раз шлёт текст «итоги готовы» и кнопку «Открыть дневник». Если за локальные сутки нет записей и практик, один раз уходит мягкое сообщение, что итоги не собраны. Повтор в те же сутки блокирует строка `daily_summary_claims` с ключом `notify:YYYY-MM-DD`. Это не новая таблица и не DDL. Ответ Telegram 403 (бот заблокирован) не повторяется в этот день. Отправка не чаще 20 сообщений в секунду.

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
