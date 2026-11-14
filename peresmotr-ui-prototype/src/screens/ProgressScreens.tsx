import { useMemo, useState } from "react";
import { demoPractices } from "../data/demo";
import { addDays, dayKey, formatShort, startOfDay, weekdayLabel } from "../format";
import { Chart, type ChartPoint } from "../components/Charts";
import { Card, SecondaryButton, SectionHeader, StatCard, TertiaryButton } from "../components/Ui";
import { useStore } from "../store";

type Period = "week" | "month" | "quarter";

const PERIODS: { id: Period; label: string; days: number }[] = [
  { id: "week", label: "Неделя", days: 7 },
  { id: "month", label: "Месяц", days: 30 },
  { id: "quarter", label: "Три месяца", days: 90 },
];

export function ProgressScreen() {
  const { state, navigate } = useStore();
  const [period, setPeriod] = useState<Period>("week");
  const meta = PERIODS.find((item) => item.id === period) ?? PERIODS[0];
  const model = useMemo(() => buildSeries(state.entries, state.completions, meta.days, period), [meta.days, period, state.completions, state.entries]);
  const todayCompletions = state.completions.filter((item) => dayKey(item.completedAt) === dayKey(new Date())).length;
  const activeDays = new Set(state.entries.map((entry) => dayKey(entry.createdAt))).size;

  return (
    <div className="screen">
      <div className="stack-lg">
        <div>
          <div className="eyebrow">Твоя история изменений</div>
          <h1 className="h1">Прогресс</h1>
          <p className="lead mt12">
            Здесь отображается реальная активность: записи дневника и выполненные практики. Это не медицинская оценка и не рейтинг психологического здоровья.
          </p>
        </div>
        <div className="seg" role="tablist" aria-label="Период">
          {PERIODS.map((item) => (
            <button key={item.id} type="button" className={period === item.id ? "on" : ""} data-testid={`period-${item.id}`} onClick={() => setPeriod(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
        <div className="stats">
          <StatCard value={String(state.entries.length)} label="записей всего" />
          <StatCard value={String(activeDays)} label="дней с записями" />
        </div>
        <Card>
          <div className="eyebrow">Выполнение практик</div>
          <p className="mt8">Всего {state.completions.length}. Сегодня {todayCompletions}.</p>
          <TertiaryButton onClick={() => navigate("/progress/history")}>Посмотреть выполненные практики</TertiaryButton>
        </Card>
        <section className="stack">
          <SectionHeader title="Динамика" />
          <Card>
            <div className="eyebrow">Активность по дням</div>
            <p className="small" style={{ color: "var(--pine)", fontWeight: 600 }}>Всего за период: {model.total} баллов активности</p>
            <Chart key={`act-${period}`} points={model.activity} ariaLabel="Активность по дням" />
            <p className="hint mt8">Запись в дневнике — 1 балл. Выполненная практика — 1 балл. График показывает баллы за каждый день выбранного периода.</p>
          </Card>
          <Card>
            <div className="eyebrow">Записи и практики</div>
            <Chart key={`split-${period}`} points={model.entries} ariaLabel="Количество записей" />
            <p className="small muted mt8">Линия записей. За период: {model.entrySum} записей и {model.practiceSum} практик.</p>
            <div className="mt16">
              <Chart key={`pr-${period}`} points={model.practices} ariaLabel="Выполненные практики" />
            </div>
          </Card>
          {state.settings.showConfidence ? (
            <Card>
              <div className="eyebrow">Уверенность в автоматических мыслях</div>
              {model.confidenceCount === 0 ? (
                <p className="small muted">В этом периоде нет записей с оценкой уверенности.</p>
              ) : (
                <>
                  <div className="conf-grid mt12">
                    <div className="conf">
                      <b>{model.before}%</b>
                      <span>в среднем до разбора</span>
                    </div>
                    <div className="conf">
                      <b>{model.after}%</b>
                      <span>в среднем после</span>
                    </div>
                  </div>
                  <div className="mt16">
                    <Chart key={`cf-${period}`} points={model.beforeSeries} ariaLabel="Уверенность до разбора" />
                  </div>
                </>
              )}
            </Card>
          ) : (
            <Card>
              <p className="small muted">Шкала уверенности скрыта в настройках. Её можно вернуть, если захочешь видеть динамику формулировок.</p>
              <TertiaryButton onClick={() => navigate("/settings")}>Открыть настройки</TertiaryButton>
            </Card>
          )}
        </section>
      </div>
    </div>
  );
}

export function PracticeHistoryScreen() {
  const { state, navigate } = useStore();
  const names = new Map(demoPractices().map((item) => [item.id, item.name]));
  const items = [...state.completions].sort((a, b) => +new Date(b.completedAt) - +new Date(a.completedAt));
  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">История</div>
          <h1 className="h1">Выполненные практики</h1>
        </div>
        {items.length === 0 ? (
          <Card>
            <p>Пока нет отмеченных практик.</p>
            <SecondaryButton full onClick={() => navigate("/practices")}>К практикам</SecondaryButton>
          </Card>
        ) : (
          items.map((item) => (
            <button key={item.id} className="list-card" type="button" onClick={() => navigate(`/practices/${item.practiceId}`)}>
              <div className="spread small muted">
                <span>{formatShort(item.completedAt)}</span>
                <span>польза {item.usefulness}/5</span>
              </div>
              <span className="thought">{names.get(item.practiceId) || "Практика"}</span>
              <span className="small muted">Сложность {item.difficultyRating}/5</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function buildSeries(
  entries: { createdAt: string; confidenceBefore: number; confidenceAfter: number }[],
  completions: { completedAt: string }[],
  days: number,
  period: Period,
) {
  const today = startOfDay(new Date());
  if (period === "quarter") {
    const buckets = 13;
    const activity: ChartPoint[] = [];
    const entrySeries: ChartPoint[] = [];
    const practiceSeries: ChartPoint[] = [];
    let total = 0;
    let entrySum = 0;
    let practiceSum = 0;
    const beforeValues: number[] = [];
    const afterValues: number[] = [];
    const beforeSeries: ChartPoint[] = [];
    for (let index = buckets - 1; index >= 0; index -= 1) {
      const end = addDays(today, -index * 7);
      const start = addDays(end, -6);
      const inRange = (iso: string) => {
        const time = +startOfDay(new Date(iso));
        return time >= +start && time <= +end;
      };
      const entryCount = entries.filter((item) => inRange(item.createdAt)).length;
      const practiceCount = completions.filter((item) => inRange(item.completedAt)).length;
      const label = formatShort(start.toISOString()).replace(".", "");
      activity.push({ label, value: entryCount + practiceCount });
      entrySeries.push({ label, value: entryCount });
      practiceSeries.push({ label, value: practiceCount });
      total += entryCount + practiceCount;
      entrySum += entryCount;
      practiceSum += practiceCount;
      const bucketEntries = entries.filter((item) => inRange(item.createdAt));
      const before = average(bucketEntries.map((item) => item.confidenceBefore));
      beforeSeries.push({ label, value: before });
      bucketEntries.forEach((item) => {
        beforeValues.push(item.confidenceBefore);
        afterValues.push(item.confidenceAfter);
      });
    }
    return {
      activity,
      entries: entrySeries,
      practices: practiceSeries,
      total,
      entrySum,
      practiceSum,
      before: average(beforeValues),
      after: average(afterValues),
      confidenceCount: beforeValues.length,
      beforeSeries,
    };
  }
  const activity: ChartPoint[] = [];
  const entrySeries: ChartPoint[] = [];
  const practiceSeries: ChartPoint[] = [];
  const beforeSeries: ChartPoint[] = [];
  let total = 0;
  let entrySum = 0;
  let practiceSum = 0;
  const beforeValues: number[] = [];
  const afterValues: number[] = [];
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const date = addDays(today, -offset);
    const key = dayKey(date);
    const dayEntries = entries.filter((item) => dayKey(item.createdAt) === key);
    const practiceCount = completions.filter((item) => dayKey(item.completedAt) === key).length;
    const label = period === "week" ? weekdayLabel(date, today) : String(date.getDate());
    activity.push({ label, value: dayEntries.length + practiceCount });
    entrySeries.push({ label, value: dayEntries.length });
    practiceSeries.push({ label, value: practiceCount });
    total += dayEntries.length + practiceCount;
    entrySum += dayEntries.length;
    practiceSum += practiceCount;
    dayEntries.forEach((item) => {
      beforeValues.push(item.confidenceBefore);
      afterValues.push(item.confidenceAfter);
    });
    beforeSeries.push({ label, value: average(dayEntries.map((item) => item.confidenceBefore)) });
  }
  return {
    activity,
    entries: entrySeries,
    practices: practiceSeries,
    total,
    entrySum,
    practiceSum,
    before: average(beforeValues),
    after: average(afterValues),
    confidenceCount: beforeValues.length,
    beforeSeries,
  };
}

function average(values: number[]): number {
  if (!values.length) return 0;
  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
}
