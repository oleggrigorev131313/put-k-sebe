import { useEffect, useState } from "react";
import { Card, SecondaryButton } from "../components/Ui";
import { dayKey } from "../format";
import { useStore } from "../store";

export function SummaryScreen({ when }: { when: "today" | "yesterday" }) {
  const { state, navigate, markSummarySeen } = useStore();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(false);
      if (when === "today") markSummarySeen();
    }, state.settings.reduceMotion ? 0 : 650);
    return () => window.clearTimeout(timer);
    // markSummarySeen is stable enough for a one-shot open; avoid retriggering the loader.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [when, state.settings.reduceMotion]);

  const recent = state.entries.filter((entry) => Date.now() - +new Date(entry.createdAt) < 2 * 86400000);
  const themes = recent.slice(0, 2).map((entry) => entry.thought);
  const emotions = Array.from(new Set(recent.map((entry) => entry.emotionGroup)));

  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">{when === "today" ? "Сегодня" : "Вчера"}</div>
          <h1 className="h1">Итоги дня</h1>
          <p className="small muted mt12">
            {when === "today"
              ? "Один обзор на календарный день. Повторно он не пересобирается, его можно только открыть."
              : "Короткий взгляд на предыдущий день — без новой генерации."}
            {state.summarySeenDate === dayKey(new Date()) && when === "today" ? " Сводка за сегодня уже отмечалась открытой." : ""}
          </p>
        </div>
        {loading ? (
          <Card>
            <div className="skeleton" />
            <div className="skeleton" style={{ width: "80%" }} />
            <div className="skeleton" style={{ width: "64%" }} />
            <p className="small muted mt12">Собираем короткие карточки, не длинный отчёт.</p>
          </Card>
        ) : (
          <>
            <Card>
              <div className="eyebrow">Коротко</div>
              <p>
                {when === "today"
                  ? `За последние дни в дневнике ${state.entries.length} записей. Свежий фокус — не переделать себя, а точнее назвать, что уже произошло.`
                  : "Вчерашний обзор держит ту же рамку: факты отдельно, выводы отдельно, один маленький шаг."}
              </p>
            </Card>
            <Card>
              <div className="eyebrow">Главные темы</div>
              <ul className="feature-list">
                {(themes.length ? themes : ["Сравнение своего результата с чужим", "Вывод о себе из одного эпизода"]).map((theme) => (
                  <li key={theme}>{theme}</li>
                ))}
              </ul>
            </Card>
            <Card>
              <div className="eyebrow">Повторяющиеся реакции</div>
              <p>{emotions.length ? emotions.join(", ") : "Стыд и вина, тревога и страх"} — чаще рядом с мыслями об оценке и молчании.</p>
            </Card>
            <Card>
              <div className="eyebrow">Наблюдения</div>
              <p>Первая формулировка звучит увереннее, чем факты, которые её держат. Более точная фраза обычно уже есть в поле «новый взгляд».</p>
            </Card>
            <Card>
              <div className="eyebrow">Гипотезы о паттернах</div>
              <p>Сравнение себя с другими и глобальный ярлык из одного события выглядят живыми. Это гипотезы для проверки, не диагнозы.</p>
            </Card>
            <Card>
              <div className="eyebrow">На завтра</div>
              <p>Один разбор свежей мысли и один конкретный шаг не длиннее 10 минут. Если дня без записей — ничего не навёрстывать.</p>
              <div className="mt16">
                <SecondaryButton full onClick={() => navigate("/practices")}>
                  Открыть практики
                </SecondaryButton>
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
