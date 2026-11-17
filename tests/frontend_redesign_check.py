"""Guard the Пересмотр redesign: survey UI is gone, sync still keeps stored answers."""
from pathlib import Path
import sys

html = Path("index.html").read_text(encoding="utf-8")

forbidden = [
    "Что сейчас больше всего беспокоит",
    "С чего начнём",
    "finish-onboarding",
    "skip-onboarding",
    "data-onboard",
    "analyze-onboarding",
    "analyzeOnboarding",
    "onboardingAreas",
    "onboardingThoughts",
    "onboardingReactions",
    "Пока пропустить",
    "Первое знакомство",
]
missing = [item for item in forbidden if item in html]
if missing:
    sys.exit("intake survey UI still present: " + ", ".join(missing))

required = [
    "ПЕРЕСМОТР",
    "УВИДЕТЬ ГЛУБЖЕ",
    "Несколько важных моментов",
    "Принять и продолжить",
    "Ознакомиться с соглашением",
    "Записать мысль",
    "Дневник мыслей",
    "Новая запись",
    "Что произошло?",
    "Шаг ",
    "Мои паттерны",
    "Добавить паттерн",
    "Мой план",
    "Все практики",
    "Уверенность в мыслях",
    "Итоги дня",
    "Краткий разбор",
    "Возможные направления",
    "Premium",
    "От 392 ₽ / месяц",
    "1 месяц · 490 ₽",
    "logo-mark.png",
    "Onest",
    "Cormorant Garamond",
    "assets/fonts/fonts.css",
    ">Год</button>",
    "https://web.tribute.tg/p/K96",
    "https://web.tribute.tg/p/KbC",
    "https://web.tribute.tg/p/KbD",
    "/v1/consents",
    "/v1/ai/analyze-entry",
    "/v1/ai/daily-summary",
    "/v1/assistant/hint",
    "/v1/state",
    "out.onboarding",
    "put-k-sebe-v1",
]
absent = [item for item in required if item not in html]
if absent:
    sys.exit("missing redesign or logic markers: " + ", ".join(absent))

if "ai_processing:true" not in html.replace(" ", "") and '{"ai_processing":true,"ai_history_storage":true}' not in html:
    sys.exit("consent accept no longer sends AI processing flags")

print("frontend redesign checks ok")
