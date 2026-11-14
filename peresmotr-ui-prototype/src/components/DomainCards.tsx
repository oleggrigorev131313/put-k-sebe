import { ArrowRight } from "lucide-react";
import { DIFFICULTY_LABEL, STATUS_LABEL } from "../data/demo";
import { emotionLabel, formatShort } from "../format";
import type { DiaryEntry, Pattern, PracticeDef } from "../types";

function Glyph({ index }: { index: number }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.4 };
  const paths = [
    <g key="a" {...common}><ellipse cx="12" cy="12" rx="7" ry="5" transform="rotate(-20 12 12)" /><ellipse cx="12" cy="12" rx="7" ry="5" transform="rotate(24 12 12)" /></g>,
    <g key="b" {...common}><circle cx="12" cy="12" r="7" /><path d="M8 12h8M12 8v8" /></g>,
    <g key="c" {...common}><circle cx="9" cy="12" r="4" /><circle cx="15" cy="12" r="4" /></g>,
    <g key="d" {...common}><path d="M5 16c2-6 4-6 7-6s5 0 7 6" /><path d="M8 10c1.2-3 2.2-3 4-3s2.8 0 4 3" /></g>,
    <g key="e" {...common}><rect x="5" y="6" width="14" height="12" rx="3" /><path d="M8 15l2.2-3 2 2 1.6-2.2L16 15" /></g>,
  ];
  return (
    <svg className="glyph" viewBox="0 0 24 24" aria-hidden="true">
      {paths[index % paths.length]}
    </svg>
  );
}

export function DiaryEntryCard({ entry, onOpen }: { entry: DiaryEntry; onOpen: () => void }) {
  return (
    <button className="list-card" type="button" onClick={onOpen}>
      <div className="list-top small muted">
        <span>{formatShort(entry.createdAt)}</span>
        <span>
          {emotionLabel(entry.emotionGroup, entry.emotionCustom)} · {entry.intensity}/10
        </span>
      </div>
      <div className="thought">{entry.thought}</div>
      <p className="small muted">Новый взгляд: {entry.newView}</p>
      <span className="linkish">
        Открыть запись <ArrowRight size={14} style={{ display: "inline", verticalAlign: "-2px" }} />
      </span>
    </button>
  );
}

export function PatternCard({ pattern, count, onOpen }: { pattern: Pattern; count: number; onOpen: () => void }) {
  const pill = pattern.status === "confirmed" ? "ok" : pattern.status === "observing" ? "warn" : pattern.status === "rejected" ? "mute" : "";
  return (
    <button className="list-card" type="button" onClick={onOpen}>
      <div className="pattern-head">
        <Glyph index={pattern.glyph} />
        <div className="grow">
          <b className="thought">{pattern.name}</b>
          <p className="small muted mt8">{pattern.description}</p>
        </div>
      </div>
      <div className="spread mt8">
        <span className="small muted">
          {count} {count === 1 ? "запись" : count >= 2 && count <= 4 ? "записи" : "записей"}
        </span>
        <span className={`pill ${pill}`}>{STATUS_LABEL[pattern.status]}</span>
      </div>
    </button>
  );
}

export function PracticeCard({
  practice,
  status,
  onOpen,
}: {
  practice: PracticeDef;
  status?: string;
  onOpen: () => void;
}) {
  return (
    <button className="list-card" type="button" onClick={onOpen}>
      <div className="spread">
        <b className="thought">{practice.name}</b>
        {status ? <span className={`pill ${status === "Выполнено" ? "ok" : ""}`}>{status}</span> : null}
      </div>
      <p className="small muted">{practice.summary}</p>
      <div className="spread small muted">
        <span>
          {practice.minutes} мин · {DIFFICULTY_LABEL[practice.difficulty]}
        </span>
        <span className="linkish">Открыть →</span>
      </div>
    </button>
  );
}

export { Glyph };
