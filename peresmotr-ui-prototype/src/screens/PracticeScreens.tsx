import { useState } from "react";
import { Check } from "lucide-react";
import { demoPractices, DIFFICULTY_LABEL } from "../data/demo";
import { dayKey, todayKey } from "../format";
import { PracticeCard } from "../components/DomainCards";
import { Card, EmptyState, PrimaryButton, SecondaryButton, SectionHeader } from "../components/Ui";
import { useStore } from "../store";
import type { PracticeDef } from "../types";

function practiceStatus(id: string, completions: { practiceId: string; completedAt: string }[], startedId: string | null, todayOnly = false): string | undefined {
  const relevant = completions.filter((item) => item.practiceId === id && (!todayOnly || dayKey(item.completedAt) === todayKey()));
  if (relevant.length) return "Выполнено";
  if (startedId === id) return "В процессе";
  return todayOnly ? "Назначено" : undefined;
}

export function PracticesScreen() {
  const { state, navigate, addSuggestionToPlan } = useStore();
  const practices = demoPractices();
  const plan = practices.filter((item) => item.kind === "plan");
  const base = practices.filter((item) => item.kind === "base");
  const suggestions = state.suggestions.filter((item) => !item.added);
  const doneToday = plan.filter((item) => practiceStatus(item.id, state.completions, state.startedPracticeId, true) === "Выполнено").length;

  return (
    <div className="screen">
      <div className="stack-lg">
        <div>
          <div className="eyebrow">Небольшие действия для осознанной работы над собой</div>
          <h1 className="h1">Практики</h1>
        </div>

        <section className="stack">
          <SectionHeader title="Персональный план" />
          {plan.map((practice) => (
            <PracticeCard
              key={practice.id}
              practice={practice}
              status={practiceStatus(practice.id, state.completions, state.startedPracticeId, true) || "В плане"}
              onOpen={() => navigate(`/practices/${practice.id}`)}
            />
          ))}
          {state.suggestions.filter((item) => item.added).map((item) => (
            <Card key={item.id}>
              <div className="spread">
                <b className="thought">{item.title}</b>
                <span className="pill ok">В плане</span>
              </div>
              <p className="small muted mt8">{item.description}</p>
              <p className="small muted mt8">{item.minutes} мин · из разбора записи</p>
            </Card>
          ))}
        </section>

        <section className="stack">
          <SectionHeader title="На сегодня" action={<span className="small muted">{doneToday} из {plan.length}</span>} />
          <Card>
            <p className="small">
              Назначено {plan.length}. Выполнено сегодня {doneToday}. Невыполненное не считается неуспехом — это просто ещё не сделано.
            </p>
            <div className="stack mt12">
              {plan.map((practice) => {
                const status = practiceStatus(practice.id, state.completions, state.startedPracticeId, true) || "Назначено";
                return (
                  <button key={practice.id} className="spread" type="button" onClick={() => navigate(`/practices/${practice.id}`)} style={{ background: "none", border: 0, padding: 0, textAlign: "left" }}>
                    <span>{practice.name}</span>
                    <span className={`pill ${status === "Выполнено" ? "ok" : status === "В процессе" ? "warn" : ""}`}>{status}</span>
                  </button>
                );
              })}
            </div>
          </Card>
        </section>

        <section className="stack">
          <SectionHeader title="Персональные задания" />
          {suggestions.length === 0 ? (
            <Card>
              <EmptyState
                title="Пока нет персональных заданий"
                text="После анализа записи персональный аналитик может предложить небольшой шаг. Ты сам решишь, добавлять ли его в план."
              />
            </Card>
          ) : (
            suggestions.map((item) => (
              <Card key={item.id}>
                <b className="thought">{item.title}</b>
                <p className="small muted mt8">{item.description}</p>
                <p className="small muted mt8">{item.minutes} мин</p>
                <div className="mt12">
                  <SecondaryButton full onClick={() => navigate("/summary")}>
                    Оставить в обзоре
                  </SecondaryButton>
                </div>
                <div className="mt12">
                  <PrimaryButton full onClick={() => addSuggestionToPlan(item.id)}>
                    Добавить в план
                  </PrimaryButton>
                </div>
              </Card>
            ))
          )}
        </section>

        <section className="stack">
          <SectionHeader title="Базовые упражнения" />
          {base.map((practice) => (
            <PracticeCard
              key={practice.id}
              practice={practice}
              status={practiceStatus(practice.id, state.completions, state.startedPracticeId)}
              onOpen={() => navigate(`/practices/${practice.id}`)}
            />
          ))}
        </section>
      </div>
    </div>
  );
}

export function PracticeDetailScreen({ id }: { id: string }) {
  const practice = demoPractices().find((item) => item.id === id);
  const { state, startPractice, completePractice, showToast, addSuggestionToPlan } = useStore();
  const [checks, setChecks] = useState<boolean[]>(() => practice?.steps.map(() => false) ?? []);
  const [rating, setRating] = useState(false);
  const [usefulness, setUsefulness] = useState(4);
  const [difficulty, setDifficulty] = useState(3);
  if (!practice) {
    return (
      <div className="screen">
        <EmptyState title="Практика не найдена" text="Вернись к списку и выбери другую." />
      </div>
    );
  }
  const doneToday = state.completions.some((item) => item.practiceId === id && dayKey(item.completedAt) === todayKey());
  const started = state.startedPracticeId === id;
  const addedSuggestion = state.suggestions.find((item) => item.title === practice.name && item.added);

  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">{practice.kind === "plan" ? "Персональный план" : "Базовое упражнение"}</div>
          <h1 className="h1" style={{ fontSize: 28 }}>{practice.name}</h1>
          <p className="lead mt12">{practice.description}</p>
        </div>
        <Card>
          <p><b>Цель. </b>{practice.goal}</p>
          <p className="mt12 small muted">
            {practice.minutes} мин · {DIFFICULTY_LABEL[practice.difficulty]}
            {doneToday ? " · выполнено сегодня" : started ? " · в процессе" : ""}
          </p>
        </Card>
        <Card>
          <div className="eyebrow">Шаги</div>
          <div className="stack">
            {practice.steps.map((step, index) => (
              <button
                key={step}
                type="button"
                className={`check ${checks[index] ? "on" : ""}`}
                onClick={() => setChecks((prev) => prev.map((value, i) => (i === index ? !value : value)))}
              >
                <i>{checks[index] ? <Check size={14} /> : null}</i>
                <span>{step}</span>
              </button>
            ))}
          </div>
        </Card>
        <div className="btn-row">
          <SecondaryButton
            data-testid="start-practice"
            onClick={() => startPractice(practice.id)}
          >
            {started || doneToday ? "Начать снова" : "Начать"}
          </SecondaryButton>
          <PrimaryButton
            data-testid="finish-practice"
            onClick={() => {
              if (!started && !doneToday) {
                showToast("Сначала начни практику — так отметка будет честной.");
                return;
              }
              setRating(true);
            }}
          >
            Завершить
          </PrimaryButton>
        </div>
        {doneToday ? <p className="hint">Сегодня практика уже есть в истории. Её можно выполнить ещё раз.</p> : null}
        {addedSuggestion ? <p className="hint">Этот шаг уже в плане.</p> : null}
        <button className="btn btn-tertiary" type="button" onClick={() => addSuggestionToPlanSafe(practice, addSuggestionToPlan, showToast)}>
          Отметить как свой шаг
        </button>
      </div>
      {rating ? (
        <div className="modal-back" style={{ position: "fixed" }} role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="rate-title" onClick={(event) => event.stopPropagation()}>
            <h2 id="rate-title">Как прошла практика?</h2>
            <p className="small muted">Короткая отметка для истории. Это не оценка тебя.</p>
            <Rate label="Полезность" value={usefulness} onChange={setUsefulness} />
            <Rate label="Сложность" value={difficulty} onChange={setDifficulty} />
            <div className="btn-row mt16">
              <SecondaryButton onClick={() => setRating(false)}>Отмена</SecondaryButton>
              <PrimaryButton
                data-testid="confirm-practice"
                onClick={() => {
                  completePractice(practice.id, usefulness, difficulty);
                  setRating(false);
                }}
              >
                Сохранить
              </PrimaryButton>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function addSuggestionToPlanSafe(
  practice: PracticeDef,
  add: (id: string) => void,
  toast: (message: string) => void,
) {
  void practice;
  void add;
  toast("Шаг уже можно открыть из плана или базовых упражнений.");
}

function Rate({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <div className="mt16">
      <div className="label">{label}</div>
      <div className="rates mt8">
        {[1, 2, 3, 4, 5].map((score) => (
          <button key={score} type="button" className={`rate ${value === score ? "on" : ""}`} onClick={() => onChange(score)}>
            {score}
          </button>
        ))}
      </div>
    </div>
  );
}
