import { BookOpenText, Layers, Sparkles, TrendingUp } from "lucide-react";
import type { ReactNode } from "react";
import { ANALYSES_LIMIT, analysesUsedToday } from "../data/demo";
import { dayKey, emotionLabel, formatShort, plural } from "../format";
import { Card, PrimaryButton, ProgressBar, SecondaryButton, SectionHeader, StatCard, TertiaryButton } from "../components/Ui";
import { useStore } from "../store";

export function HomeScreen() {
  const { state, navigate } = useStore();
  const used = analysesUsedToday(state);
  const remaining = Math.max(0, ANALYSES_LIMIT - used);
  const days = new Set(state.entries.map((entry) => dayKey(entry.createdAt))).size;
  const lastEntry = [...state.entries].sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt))[0];
  const lastPractices = [...state.completions]
    .sort((a, b) => +new Date(b.completedAt) - +new Date(a.completedAt))
    .slice(0, 2);

  const practiceName = (id: string) => statePracticesName(id);

  return (
    <div className={`screen ${state.settings.compactCards ? "compact" : ""}`}>
      <div className="stack-lg">
        <Card hero>
          <h1 className="h1">Привет!</h1>
          <p className="lead mt12">
            Не нужно менять всю жизнь за один день. Достаточно лучше понять себя и сделать следующий небольшой шаг.
          </p>
          <div className="mt16">
            <PrimaryButton full data-testid="home-new-entry" onClick={() => navigate("/diary/new")}>
              Записать мысль →
            </PrimaryButton>
          </div>
        </Card>

        <section className="stack">
          <SectionHeader title="Персональный аналитик" />
          <Card>
            <div className="eyebrow">Сегодня</div>
            <p className="small">
              Использовано {used} из {ANALYSES_LIMIT} анализов за сегодня · осталось {remaining}
            </p>
            <div className="mt12">
              <ProgressBar value={used} max={ANALYSES_LIMIT} />
            </div>
            <div className="eyebrow mt16">Ежедневный обзор</div>
            <p className="small muted">
              Один персональный обзор в день: что происходило, какие мысли и реакции повторялись, что удалось заметить и какие небольшие шаги могут помочь завтра.
            </p>
            <div className="mt16">
              <SecondaryButton full data-testid="open-summary" onClick={() => navigate("/summary")}>
                Посмотреть итоги дня
              </SecondaryButton>
            </div>
            <TertiaryButton onClick={() => navigate("/summary/yesterday")}>Смотреть итоги предыдущего дня</TertiaryButton>
          </Card>
        </section>

        <section className="stack">
          <SectionHeader title="Твой путь" />
          <div className="stats">
            <StatCard value={String(state.entries.length)} label={plural(state.entries.length, "запись", "записи", "записей")} />
            <StatCard value={String(days)} label={plural(days, "день с записями", "дня с записями", "дней с записями")} />
          </div>
        </section>

        <section className="stack">
          <SectionHeader title="Что можно сделать" />
          <Action icon={<BookOpenText size={18} />} title="Разобрать мысль" text="Новая запись в дневнике" onClick={() => navigate("/diary/new")} />
          <Action icon={<Sparkles size={18} />} title="Мои паттерны" text="Гипотезы, не диагнозы" onClick={() => navigate("/patterns")} />
          <Action icon={<Layers size={18} />} title="Выполнить практику" text="Короткое упражнение" onClick={() => navigate("/practices")} />
          <Action icon={<TrendingUp size={18} />} title="Посмотреть прогресс" text="Записи и сделанные практики" onClick={() => navigate("/progress")} />
        </section>

        <section className="stack">
          <SectionHeader title="Последняя активность" />
          {lastEntry ? (
            <Card>
              <div className="eyebrow">Дневник · {formatShort(lastEntry.createdAt)}</div>
              <p className="thought">{lastEntry.thought}</p>
              <p className="small muted mt8">
                {emotionLabel(lastEntry.emotionGroup, lastEntry.emotionCustom)} · {lastEntry.intensity}/10
              </p>
              <TertiaryButton onClick={() => navigate(`/diary/${lastEntry.id}`)}>Открыть запись</TertiaryButton>
            </Card>
          ) : null}
          {lastPractices.map((item) => (
            <Card key={item.id}>
              <div className="eyebrow">Практика · {formatShort(item.completedAt)}</div>
              <p className="thought">{practiceName(item.practiceId)}</p>
              <p className="small muted mt8">
                Полезность {item.usefulness}/5 · сложность {item.difficultyRating}/5
              </p>
              <TertiaryButton onClick={() => navigate(`/practices/${item.practiceId}`)}>Открыть практику</TertiaryButton>
            </Card>
          ))}
        </section>
      </div>
    </div>
  );
}

function statePracticesName(id: string): string {
  const names: Record<string, string> = {
    "plan-thought": "Разобрать одну автоматическую мысль",
    "plan-compare": "Заметить сравнение с другими",
    "plan-step": "Один конкретный следующий шаг",
    "base-check": "Проверка автоматической мысли",
    "base-alt": "Альтернативный взгляд",
    "base-critic": "Работа с самокритикой",
    "base-facts": "Разделение фактов и интерпретаций",
    "base-anxiety": "Снижение тревоги",
    "base-observe": "Осознанное наблюдение за эмоциями",
  };
  return names[id] || "Практика";
}

function Action({ icon, title, text, onClick }: { icon: ReactNode; title: string; text: string; onClick: () => void }) {
  return (
    <button className="row-link" type="button" onClick={onClick}>
      <span className="row-ico">{icon}</span>
      <span className="grow">
        <b>{title}</b>
        <small>{text}</small>
      </span>
    </button>
  );
}
