import { useState } from "react";
import { STATUS_LABEL } from "../data/demo";
import { emotionLabel, formatShort, plural } from "../format";
import { PatternCard } from "../components/DomainCards";
import { Card, EmptyState, PrimaryButton, SecondaryButton, Textarea } from "../components/Ui";
import { useStore } from "../store";
import type { PatternStatus } from "../types";

const STATUSES: PatternStatus[] = ["proposed", "confirmed", "rejected", "observing"];

export function PatternsScreen() {
  const { state, navigate } = useStore();
  return (
    <div className="screen">
      <div className="stack-lg">
        <div>
          <div className="eyebrow">Повторяющиеся сценарии мышления</div>
          <h1 className="h1">Мои паттерны</h1>
          <p className="lead mt12">
            Паттерны — это рабочие гипотезы о повторяющихся способах думать и действовать. Они помогают замечать привычные реакции, но не являются диагнозами.
          </p>
        </div>
        <PrimaryButton full data-testid="new-pattern" onClick={() => navigate("/patterns/new")}>
          + Добавить паттерн
        </PrimaryButton>
        {state.patterns.map((pattern) => (
          <PatternCard
            key={pattern.id}
            pattern={pattern}
            count={state.entries.filter((entry) => entry.patternId === pattern.id).length}
            onOpen={() => navigate(`/patterns/${pattern.id}`)}
          />
        ))}
      </div>
    </div>
  );
}

export function PatternDetailScreen({ id }: { id: string }) {
  const { state, navigate, setPatternStatus } = useStore();
  const pattern = state.patterns.find((item) => item.id === id);
  if (!pattern) {
    return (
      <div className="screen">
        <EmptyState title="Паттерн не найден" text="Его нет в данных этого браузера." />
      </div>
    );
  }
  const linked = state.entries.filter((entry) => entry.patternId === pattern.id);
  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">Гипотеза</div>
          <h1 className="h1" style={{ fontSize: 28 }}>{pattern.name}</h1>
        </div>
        <Card>
          <div className="eyebrow">Описание</div>
          <p>{pattern.description}</p>
          <div className="eyebrow mt16">Как проявляется</div>
          <p>{pattern.howItShows}</p>
        </Card>
        <Card>
          <div className="eyebrow">Другие способы увидеть</div>
          <ul className="feature-list">
            {pattern.alternatives.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </Card>
        <Card>
          <div className="eyebrow">Статус</div>
          <p className="small muted">Сейчас: {STATUS_LABEL[pattern.status]}. Статус можно сменить — это твоя оценка гипотезы.</p>
          <div className="stack mt12">
            {STATUSES.map((status) => (
              <button
                key={status}
                type="button"
                className={`emotion ${pattern.status === status ? "active" : ""}`}
                data-testid={`status-${status}`}
                onClick={() => setPatternStatus(pattern.id, status)}
              >
                {STATUS_LABEL[status]}
              </button>
            ))}
          </div>
        </Card>
        <Card>
          <div className="eyebrow">Связанные записи · {linked.length}</div>
          {linked.length === 0 ? (
            <p className="small muted">Пока ни одна запись не связана с этой гипотезой.</p>
          ) : (
            linked.map((entry) => (
              <button key={entry.id} className="list-card mt12" type="button" onClick={() => navigate(`/diary/${entry.id}`)}>
                <span className="small muted">{formatShort(entry.createdAt)} · {emotionLabel(entry.emotionGroup, entry.emotionCustom)}</span>
                <span className="thought">{entry.thought}</span>
              </button>
            ))
          )}
        </Card>
      </div>
    </div>
  );
}

export function PatternCreateScreen() {
  const { addPattern, goBack } = useStore();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [howItShows, setHow] = useState("");
  const [alternatives, setAlternatives] = useState("");
  const [status, setStatus] = useState<PatternStatus>("proposed");
  const ready = name.trim().length > 1 && description.trim().length > 1;
  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">Новая гипотеза</div>
          <h1 className="h1">Добавить паттерн</h1>
          <p className="small muted mt12">Это рабочая формулировка для наблюдения. Её можно позже подтвердить, отклонить или оставить под вопросом.</p>
        </div>
        <Card>
          <div className="stack">
            <label className="field">
              <span className="label">Название</span>
              <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Например: чтение мыслей" />
            </label>
            <Textarea label="Короткое описание" value={description} onChange={(event) => setDescription(event.target.value)} />
            <Textarea label="Как это проявляется" value={howItShows} onChange={(event) => setHow(event.target.value)} />
            <Textarea
              label="Альтернативные формулировки"
              hint="Каждая строка — отдельный способ увидеть ситуацию точнее."
              value={alternatives}
              onChange={(event) => setAlternatives(event.target.value)}
            />
            <div className="field">
              <span className="label">Статус</span>
              {STATUSES.map((item) => (
                <button key={item} type="button" className={`emotion ${status === item ? "active" : ""}`} onClick={() => setStatus(item)}>
                  {STATUS_LABEL[item]}
                </button>
              ))}
            </div>
            <PrimaryButton
              full
              disabled={!ready}
              data-testid="save-pattern"
              onClick={() =>
                addPattern({
                  name: name.trim(),
                  description: description.trim(),
                  howItShows: howItShows.trim() || "Пока отмечаю, в каких ситуациях это всплывает.",
                  alternatives: alternatives.split("\n").map((line) => line.trim()).filter(Boolean).slice(0, 6).concat(
                    alternatives.trim() ? [] : ["Это гипотеза, её можно проверить на следующих записях."],
                  ),
                  status,
                  glyph: name.length % 5,
                })
              }
            >
              Сохранить паттерн
            </PrimaryButton>
            {!ready ? <p className="hint">Нужны название и описание.</p> : null}
          </div>
        </Card>
        <SecondaryButton full onClick={goBack}>
          Назад без сохранения
        </SecondaryButton>
        <p className="hint">
          {plural(name.trim().length, "символ", "символа", "символов")} в названии: {name.trim().length}
        </p>
      </div>
    </div>
  );
}
