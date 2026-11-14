export const EMOTION_GROUPS = [
  "Тревога и страх",
  "Грусть и утрата",
  "Злость и раздражение",
  "Стыд и вина",
  "Другие состояния",
] as const;

export type EmotionGroup = (typeof EMOTION_GROUPS)[number];

export type PatternStatus = "proposed" | "confirmed" | "rejected" | "observing";

export type Difficulty = "easy" | "medium" | "deep";

export interface DiaryEntry {
  id: string;
  createdAt: string;
  situation: string;
  thought: string;
  emotionGroup: EmotionGroup;
  emotionCustom: string;
  intensity: number;
  factsFor: string;
  factsAgainst: string;
  newView: string;
  nextStep: string;
  confidenceBefore: number;
  confidenceAfter: number;
  patternId?: string;
  analysis?: {
    fact: string;
    patternNote: string;
    step: string;
  };
}

export interface Pattern {
  id: string;
  name: string;
  description: string;
  howItShows: string;
  alternatives: string[];
  status: PatternStatus;
  glyph: number;
}

export interface PracticeDef {
  id: string;
  name: string;
  summary: string;
  description: string;
  goal: string;
  minutes: number;
  difficulty: Difficulty;
  steps: string[];
  kind: "plan" | "base";
}

export interface Completion {
  id: string;
  practiceId: string;
  completedAt: string;
  usefulness: number;
  difficultyRating: number;
}

export interface Suggestion {
  id: string;
  title: string;
  description: string;
  minutes: number;
  fromEntryId?: string;
  added: boolean;
}

export interface Settings {
  name: string;
  dailyReminder: boolean;
  showConfidence: boolean;
  compactCards: boolean;
  reduceMotion: boolean;
}

export interface NotificationItem {
  id: string;
  title: string;
  body: string;
  at: string;
  read: boolean;
}

export interface Draft {
  step: number;
  situation: string;
  thought: string;
  emotionGroup: EmotionGroup | "";
  emotionCustom: string;
  intensity: number;
  factsFor: string;
  factsAgainst: string;
  newView: string;
  nextStep: string;
  confidenceBefore: number;
  confidenceAfter: number;
}

export interface AppState {
  onboarded: boolean;
  entries: DiaryEntry[];
  patterns: Pattern[];
  completions: Completion[];
  suggestions: Suggestion[];
  settings: Settings;
  notifications: NotificationItem[];
  analysesUsed: number;
  analysesDate: string;
  plan: "free" | "premium";
  summarySeenDate: string | null;
  startedPracticeId: string | null;
  draft: Draft | null;
}

export type Route =
  | { name: "welcome" }
  | { name: "account" }
  | { name: "home" }
  | { name: "diary" }
  | { name: "wizard" }
  | { name: "entry"; id: string }
  | { name: "patterns" }
  | { name: "pattern-new" }
  | { name: "pattern"; id: string }
  | { name: "practices" }
  | { name: "practice"; id: string }
  | { name: "progress" }
  | { name: "practice-history" }
  | { name: "summary"; when: "today" | "yesterday" }
  | { name: "premium" }
  | { name: "profile" }
  | { name: "settings" }
  | { name: "notifications" }
  | { name: "tariff" };

export type TabId = "home" | "diary" | "patterns" | "progress" | "practices";

export interface ModalRequest {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
  dismissable?: boolean;
  onConfirm?: () => void;
}
