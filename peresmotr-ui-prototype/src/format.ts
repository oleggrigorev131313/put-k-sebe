const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "нояб", "дек"];
const MONTHS_FULL = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

export function dayKey(input: string | Date): string {
  const d = typeof input === "string" ? new Date(input) : input;
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

export function todayKey(now = new Date()): string {
  return dayKey(now);
}

export function atDay(offset: number, hours = 11): string {
  const d = new Date();
  d.setDate(d.getDate() - offset);
  d.setHours(hours, 12, 0, 0);
  return d.toISOString();
}

export function formatShort(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}.`;
}

export function formatFull(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()} ${MONTHS_FULL[d.getMonth()]} ${d.getFullYear()}`;
}

export function weekdayLabel(date: Date, today: Date): string {
  if (dayKey(date) === dayKey(today)) return "сег";
  return WEEKDAYS[date.getDay()];
}

export function plural(n: number, one: string, few: string, many: string): string {
  const n10 = n % 10;
  const n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 10 || n100 >= 20)) return few;
  return many;
}

export function uid(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function emotionLabel(group: string, custom: string): string {
  if (group === "Другие состояния" && custom.trim()) return custom.trim();
  return group;
}

export function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function startOfDay(date: Date): Date {
  const next = new Date(date);
  next.setHours(0, 0, 0, 0);
  return next;
}
