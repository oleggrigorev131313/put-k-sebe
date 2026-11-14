import { useMemo, useState } from "react";
import { demoPractices } from "../data/demo";
import { dayKey, emotionLabel, formatFull, todayKey } from "../format";
import { DiaryEntryCard } from "../components/DomainCards";
import {
  Card,
  ConfidenceSlider,
  EmotionSelector,
  EmptyState,
  IntensitySlider,
  PrimaryButton,
  SecondaryButton,
  TertiaryButton,
  Textarea,
} from "../components/Ui";
import { useStore } from "../store";
import { EMOTION_GROUPS, type Draft, type EmotionGroup } from "../types";
import { emptyDraft } from "../data/demo";

type DateFilter = "all" | "today" | "7" | "30";

export function DiaryScreen() {
  const { state, navigate } = useStore();
  const [emotion, setEmotion] = useState<EmotionGroup | "all">("all");
  const [period, setPeriod] = useState<DateFilter>("all");
  const entries = useMemo(() => {
    const now = Date.now();
    return [...state.entries]
      .filter((entry) => (emotion === "all" ? true : entry.emotionGroup === emotion))
      .filter((entry) => {
        if (period === "all") return true;
        if (period === "today") return dayKey(entry.createdAt) === todayKey();
        const days = period === "7" ? 7 : 30;
        return now - +new Date(entry.createdAt) <= days * 86400000;
      })
      .sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt));
  }, [emotion, period, state.entries]);

  return (
    <div className="screen">
      <div className="stack-lg">
        <div>
          <div className="eyebrow">Наблюдение без осуждения</div>
          <h1 className="h1">Дневник мыслей</h1>
          <p className="lead mt12">Записи помогают замечать связи между ситуациями, мыслями, эмоциями и действиями.</p>
        </div>
        <PrimaryButton full data-testid="new-entry" onClick={() => navigate("/diary/new")}>
          + Новая запись
        </PrimaryButton>
        <div className="stack">
          <div className="chips" aria-label="Фильтр по эмоции">
            <button className={`chip ${emotion === "all" ? "active" : ""}`} type="button" onClick={() => setEmotion("all")}>
              Все состояния
            </button>
            {EMOTION_GROUPS.map((group) => (
              <button key={group} className={`chip ${emotion === group ? "active" : ""}`} type="button" onClick={() => setEmotion(group)}>
                {group}
              </button>
            ))}
          </div>
          <div className="chips" aria-label="Фильтр по дате">
            {(
              [
                ["all", "Все даты"],
                ["today", "Сегодня"],
                ["7", "7 дней"],
                ["30", "30 дней"],
              ] as const
            ).map(([id, label]) => (
              <button key={id} className={`chip ${period === id ? "active" : ""}`} type="button" onClick={() => setPeriod(id)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <SectionTitle>История записей</SectionTitle>
        {entries.length === 0 ? (
          <Card>
            <EmptyState title="Нет записей по этому фильтру" text="Сними фильтр или добавь новую запись — она появится здесь." />
            <SecondaryButton full onClick={() => { setEmotion("all"); setPeriod("all"); }}>
              Сбросить фильтры
            </SecondaryButton>
          </Card>
        ) : (
          entries.map((entry) => <DiaryEntryCard key={entry.id} entry={entry} onOpen={() => navigate(`/diary/${entry.id}`)} />)
        )}
      </div>
    </div>
  );
}

function SectionTitle({ children }: { children: string }) {
  return <h2 className="h2">{children}</h2>;
}

const STEP_TITLES = [
  "Что произошло?",
  "Что ты подумал в этот момент?",
  "Какая эмоция ближе?",
  "Что говорят факты?",
  "Новый взгляд",
  "Следующий небольшой шаг",
];

export function WizardScreen() {
  const { state, saveDraft, saveEntry, openModal, closeModal, goBack } = useStore();
  const [draft, setDraft] = useState<Draft>(state.draft ?? emptyDraft());
  const [direction, setDirection] = useState(1);

  function update(patch: Partial<Draft>) {
    setDraft((prev) => {
      const next = { ...prev, ...patch };
      queueMicrotask(() => saveDraft(next));
      return next;
    });
  }

  function valid(step = draft.step): boolean {
    if (step === 0) return draft.situation.trim().length > 1;
    if (step === 1) return draft.thought.trim().length > 1;
    if (step === 2) return Boolean(draft.emotionGroup) && (draft.emotionGroup !== "Другие состояния" || draft.emotionCustom.trim().length > 1);
    if (step === 3) return draft.factsFor.trim().length > 1 && draft.factsAgainst.trim().length > 1;
    if (step === 4) return draft.newView.trim().length > 1;
    return draft.nextStep.trim().length > 1;
  }

  function goStep(next: number) {
    setDirection(next > draft.step ? 1 : -1);
    update({ step: next });
  }

  function cancel() {
    openModal({
      title: "Закрыть запись?",
      body: "Черновик сохранится в этом браузере, пока ты не сбросишь демо-данные.",
      confirmLabel: "Закрыть",
      cancelLabel: "Остаться",
      onConfirm: () => {
        closeModal();
        goBack();
      },
    });
  }

  return (
    <div className="screen">
      <div className="stack">
        <div className="eyebrow">Рабочая тетрадь · шаг {draft.step + 1} из 6</div>
        <h1 className="h1">{STEP_TITLES[draft.step]}</h1>
        <div className="steps" aria-label="Шаги записи">
          {STEP_TITLES.map((title, index) => (
            <button
              key={title}
              className={`step-dot ${index === draft.step ? "on" : ""} ${index < draft.step ? "done" : ""}`}
              type="button"
              aria-label={title}
              disabled={index > draft.step}
              onClick={() => index < draft.step && goStep(index)}
            >
              {index + 1}
            </button>
          ))}
        </div>
        <Card>
          <div className="step-pane" key={`${draft.step}-${direction}`}>
            {draft.step === 0 ? (
              <Textarea
                label="Опиши конкретную ситуацию"
                hint="Например: отправил предложение клиенту, но не получил ответ."
                value={draft.situation}
                onChange={(event) => update({ situation: event.target.value })}
              />
            ) : null}
            {draft.step === 1 ? (
              <div className="stack">
                <Textarea
                  label="Автоматическая мысль"
                  hint="Первая формулировка, которая пришла сама. Запиши её как есть."
                  value={draft.thought}
                  onChange={(event) => update({ thought: event.target.value })}
                />
                {state.settings.showConfidence ? (
                  <ConfidenceSlider
                    label="Насколько мысль кажется убедительной сейчас"
                    value={draft.confidenceBefore}
                    onChange={(value) => update({ confidenceBefore: value })}
                  />
                ) : null}
              </div>
            ) : null}
            {draft.step === 2 ? (
              <div className="stack">
                <EmotionSelector
                  value={draft.emotionGroup}
                  custom={draft.emotionCustom}
                  onChange={(emotionGroup) => update({ emotionGroup })}
                  onCustom={(emotionCustom) => update({ emotionCustom })}
                />
                <IntensitySlider value={draft.intensity} onChange={(intensity) => update({ intensity })} />
              </div>
            ) : null}
            {draft.step === 3 ? (
              <div className="stack">
                <Textarea label="Что подтверждает эту мысль" value={draft.factsFor} onChange={(event) => update({ factsFor: event.target.value })} />
                <Textarea label="Что ей противоречит" value={draft.factsAgainst} onChange={(event) => update({ factsAgainst: event.target.value })} />
              </div>
            ) : null}
            {draft.step === 4 ? (
              <div className="stack">
                <Textarea
                  label="Как можно увидеть ситуацию точнее"
                  hint="Не обесценивая чувство и не заставляя себя «мыслить позитивно»."
                  value={draft.newView}
                  onChange={(event) => update({ newView: event.target.value })}
                />
                {state.settings.showConfidence ? (
                  <ConfidenceSlider
                    label="Насколько убедительна мысль теперь"
                    value={draft.confidenceAfter}
                    onChange={(value) => update({ confidenceAfter: value })}
                  />
                ) : null}
              </div>
            ) : null}
            {draft.step === 5 ? (
              <Textarea
                label="Одно конкретное действие"
                hint="Не план на жизнь. То, что можно сделать сегодня."
                value={draft.nextStep}
                onChange={(event) => update({ nextStep: event.target.value })}
              />
            ) : null}
            {draft.step === 0 ? (
              <p className="note mt16">
                Не нужно заставлять себя мыслить позитивно. Цель — посмотреть на ситуацию чуть точнее и выбрать следующий посильный шаг.
              </p>
            ) : null}
          </div>
          <div className="btn-row mt16">
            <SecondaryButton type="button" onClick={() => (draft.step === 0 ? cancel() : goStep(draft.step - 1))} data-testid="wizard-back">
              {draft.step === 0 ? "Отменить" : "Назад"}
            </SecondaryButton>
            {draft.step < 5 ? (
              <PrimaryButton type="button" disabled={!valid()} data-testid="wizard-next" onClick={() => goStep(draft.step + 1)}>
                Продолжить
              </PrimaryButton>
            ) : (
              <PrimaryButton
                type="button"
                disabled={!valid()}
                data-testid="wizard-save"
                onClick={() => {
                  if (!draft.emotionGroup) return;
                  saveEntry({
                    situation: draft.situation.trim(),
                    thought: draft.thought.trim(),
                    emotionGroup: draft.emotionGroup,
                    emotionCustom: draft.emotionCustom.trim(),
                    intensity: draft.intensity,
                    factsFor: draft.factsFor.trim(),
                    factsAgainst: draft.factsAgainst.trim(),
                    newView: draft.newView.trim(),
                    nextStep: draft.nextStep.trim(),
                    confidenceBefore: draft.confidenceBefore,
                    confidenceAfter: draft.confidenceAfter,
                  });
                }}
              >
                Сохранить запись
              </PrimaryButton>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

export function EntryScreen({ id }: { id: string }) {
  const { state, navigate, linkPattern, deleteEntry, openModal, closeModal, commitAnalysis, showToast } = useStore();
  const entry = state.entries.find((item) => item.id === id);
  const [loading, setLoading] = useState(false);
  const [patternId, setPatternId] = useState(entry?.patternId ?? "");
  if (!entry) {
    return (
      <div className="screen">
        <EmptyState title="Запись не найдена" text="Возможно, она была удалена в этом браузере." />
        <SecondaryButton full onClick={() => navigate("/diary")}>К дневнику</SecondaryButton>
      </div>
    );
  }
  const label = emotionLabel(entry.emotionGroup, entry.emotionCustom);
  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">{formatFull(entry.createdAt)}</div>
          <h1 className="h1" style={{ fontSize: 26 }}>{entry.thought}</h1>
        </div>
        <Card>
          <Field k="Ситуация" v={entry.situation} />
          <Field k="Эмоция" v={`${label} · ${entry.intensity}/10`} />
          <Field k="Что подтверждает" v={entry.factsFor} />
          <Field k="Что противоречит" v={entry.factsAgainst} />
          <Field k="Новый взгляд" v={entry.newView} />
          <Field k="Следующий шаг" v={entry.nextStep} />
          {state.settings.showConfidence ? (
            <Field k="Уверенность" v={`${entry.confidenceBefore}% → ${entry.confidenceAfter}%`} />
          ) : null}
        </Card>
        <Card>
          <label className="field">
            <span className="label">Связать с паттерном</span>
            <select className="select" value={patternId} onChange={(event) => setPatternId(event.target.value)}>
              <option value="">Без связи</option>
              {state.patterns.map((pattern) => (
                <option key={pattern.id} value={pattern.id}>{pattern.name}</option>
              ))}
            </select>
          </label>
          <div className="mt12">
            <SecondaryButton full onClick={() => linkPattern(entry.id, patternId || undefined)}>
              Сохранить связь
            </SecondaryButton>
          </div>
        </Card>
        {entry.analysis ? (
          <Card>
            <div className="eyebrow">Разбор записи</div>
            <Field k="Точнее" v={entry.analysis.fact} />
            <Field k="Паттерн" v={entry.analysis.patternNote} />
            <Field k="Шаг" v={entry.analysis.step} />
          </Card>
        ) : (
          <PrimaryButton
            full
            loading={loading}
            data-testid="analyze-entry"
            onClick={() => {
              setLoading(true);
              window.setTimeout(() => {
                const result = commitAnalysis(entry.id);
                setLoading(false);
                if (result === "locked") {
                  openModal({
                    title: "Разбор записи — в Premium",
                    body: "Краткий анализ отдельной записи относится к персональному аналитику. Дневник и итоги дня остаются доступны и без него. Оплаты в прототипе нет.",
                    confirmLabel: "Смотреть тарифы",
                    cancelLabel: "Закрыть",
                    onConfirm: () => {
                      closeModal();
                      navigate("/premium");
                    },
                  });
                } else if (result === "limit") {
                  showToast("На сегодня разборы закончились. Лимит — 5.");
                }
              }, 700);
            }}
          >
            Разобрать запись
          </PrimaryButton>
        )}
        <button
          className="btn btn-danger btn-full"
          type="button"
          onClick={() =>
            openModal({
              title: "Удалить запись?",
              body: "Она исчезнет из истории этого браузера. Сброс демо-данных вернёт исходные примеры.",
              confirmLabel: "Удалить",
              cancelLabel: "Оставить",
              tone: "danger",
              onConfirm: () => {
                closeModal();
                deleteEntry(entry.id);
              },
            })
          }
        >
          Удалить запись
        </button>
        <p className="hint">В библиотеке практик есть «{demoPractices()[3].name}», если захочется продолжить отдельно.</p>
        <TertiaryButton onClick={() => navigate("/practices")}>К практикам</TertiaryButton>
      </div>
    </div>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <p className="mt12">
      <b>{k}. </b>
      <span>{v}</span>
    </p>
  );
}
