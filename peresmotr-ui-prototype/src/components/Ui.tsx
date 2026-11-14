import { ArrowRight } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";
import { EMOTION_GROUPS, type EmotionGroup } from "../types";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  loading?: boolean;
  full?: boolean;
};

function ButtonBody({ loading, children }: { loading?: boolean; children: ReactNode }) {
  return (
    <>
      {loading ? <span className="spinner" aria-hidden /> : null}
      <span>{children}</span>
    </>
  );
}

export function PrimaryButton({ loading, full, children, className = "", disabled, ...rest }: ButtonProps) {
  return (
    <button className={`btn btn-primary ${full ? "btn-full" : ""} ${className}`} disabled={Boolean(loading || disabled)} {...rest}>
      <ButtonBody loading={loading}>{children}</ButtonBody>
    </button>
  );
}

export function SecondaryButton({ loading, full, children, className = "", disabled, ...rest }: ButtonProps) {
  return (
    <button className={`btn btn-secondary ${full ? "btn-full" : ""} ${className}`} disabled={Boolean(loading || disabled)} {...rest}>
      <ButtonBody loading={loading}>{children}</ButtonBody>
    </button>
  );
}

export function TertiaryButton({ children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={`btn btn-tertiary ${className}`} {...rest}>
      <span>{children}</span>
      <ArrowRight size={16} aria-hidden />
    </button>
  );
}

export function Card({ children, className = "", hero = false }: { children: ReactNode; className?: string; hero?: boolean }) {
  return <section className={`card ${hero ? "card-hero" : ""} ${className}`}>{children}</section>;
}

export function StatCard({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

export function SectionHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="section-head">
      <h2>{title}</h2>
      {action}
    </div>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  const { label, id, ...rest } = props;
  const fieldId = id || label;
  return (
    <label className="field" htmlFor={fieldId}>
      <span className="label">{label}</span>
      <input id={fieldId} className="input" {...rest} />
    </label>
  );
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hint?: string }) {
  const { label, hint, id, ...rest } = props;
  const fieldId = id || label;
  return (
    <label className="field" htmlFor={fieldId}>
      <span className="label">{label}</span>
      <textarea id={fieldId} className="textarea" {...rest} />
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

export function EmotionSelector({
  value,
  custom,
  onChange,
  onCustom,
}: {
  value: EmotionGroup | "";
  custom: string;
  onChange: (value: EmotionGroup) => void;
  onCustom: (value: string) => void;
}) {
  return (
    <div className="emotions">
      {EMOTION_GROUPS.map((group) => (
        <button
          key={group}
          type="button"
          className={`emotion ${value === group ? "active" : ""}`}
          aria-pressed={value === group}
          onClick={() => onChange(group)}
        >
          {group}
        </button>
      ))}
      {value === "Другие состояния" ? (
        <input
          className="input"
          value={custom}
          placeholder="Своё слово для состояния"
          onChange={(event) => onCustom(event.target.value)}
          aria-label="Своё название состояния"
        />
      ) : null}
    </div>
  );
}

export function ProgressBar({ value, max = 100 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <LogoMark />
      <h3>{title}</h3>
      <p className="muted small">{text}</p>
    </div>
  );
}

function LogoMark() {
  return (
    <svg className="mark" width="46" height="32" viewBox="0 0 144 100" aria-hidden="true">
      <g fill="none" stroke="#465752" strokeWidth="8">
        <ellipse cx="58" cy="52" rx="44" ry="36" transform="rotate(-16 58 52)" />
        <ellipse cx="90" cy="48" rx="44" ry="36" transform="rotate(14 90 48)" />
      </g>
    </svg>
  );
}

export function IntensitySlider({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const pct = (value / 10) * 100;
  return (
    <div className="field">
      <div className="slider-row">
        <span className="label">Интенсивность</span>
        <span className="slider-value">{value}/10</span>
      </div>
      <input
        type="range"
        min={0}
        max={10}
        step={1}
        value={value}
        aria-valuetext={`${value} из 10`}
        style={{ ["--track" as string]: `linear-gradient(90deg, #465752 ${pct}%, #e6e3dc ${pct}%)` }}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

export function ConfidenceSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="field">
      <div className="slider-row">
        <span className="label">{label}</span>
        <span className="slider-value">{value}%</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={value}
        style={{ ["--track" as string]: `linear-gradient(90deg, #465752 ${value}%, #e6e3dc ${value}%)` }}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}
