import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ANALYSES_LIMIT, analysesUsedToday, createSeed, loadState } from "./data/demo";
import { parentPath } from "./components/Shell";
import { dayKey, todayKey, uid } from "./format";
import type { AppState, DiaryEntry, Draft, ModalRequest, Pattern, PatternStatus, Route } from "./types";

const KEY_SAVE = "peresmotr-proto-v1";

function parseRoute(hash: string): Route {
  const parts = (hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  const [a, b] = parts;
  if (!a || a === "welcome") return { name: "welcome" };
  if (a === "account") return { name: "account" };
  if (a === "home") return { name: "home" };
  if (a === "diary" && b === "new") return { name: "wizard" };
  if (a === "diary" && b) return { name: "entry", id: b };
  if (a === "diary") return { name: "diary" };
  if (a === "patterns" && b === "new") return { name: "pattern-new" };
  if (a === "patterns" && b) return { name: "pattern", id: b };
  if (a === "patterns") return { name: "patterns" };
  if (a === "practices" && b) return { name: "practice", id: b };
  if (a === "practices") return { name: "practices" };
  if (a === "progress" && b === "history") return { name: "practice-history" };
  if (a === "progress") return { name: "progress" };
  if (a === "summary" && b === "yesterday") return { name: "summary", when: "yesterday" };
  if (a === "summary") return { name: "summary", when: "today" };
  if (a === "premium") return { name: "premium" };
  if (a === "profile") return { name: "profile" };
  if (a === "settings") return { name: "settings" };
  if (a === "notifications") return { name: "notifications" };
  if (a === "tariff") return { name: "tariff" };
  return { name: "home" };
}

interface StoreValue {
  state: AppState;
  route: Route;
  toast: string | null;
  modal: ModalRequest | null;
  navigate: (path: string) => void;
  goBack: () => void;
  showToast: (message: string) => void;
  openModal: (modal: ModalRequest) => void;
  closeModal: () => void;
  setOnboarded: () => void;
  updateSettings: (patch: Partial<AppState["settings"]>) => void;
  resetDemo: () => void;
  saveDraft: (draft: Draft | null) => void;
  saveEntry: (entry: Omit<DiaryEntry, "id" | "createdAt">) => void;
  deleteEntry: (id: string) => void;
  linkPattern: (entryId: string, patternId?: string) => void;
  addPattern: (pattern: Omit<Pattern, "id">) => string;
  setPatternStatus: (id: string, status: PatternStatus) => void;
  startPractice: (id: string) => void;
  completePractice: (id: string, usefulness: number, difficultyRating: number) => void;
  addSuggestionToPlan: (id: string) => void;
  commitAnalysis: (id: string) => "ok" | "locked" | "limit";
  setPlan: (plan: "free" | "premium") => void;
  markNotification: (id: string) => void;
  markAllNotifications: () => void;
  markSummarySeen: () => void;
}

const StoreContext = createContext<StoreValue | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AppState>(() => loadState());
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [toast, setToast] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalRequest | null>(null);

  useEffect(() => {
    localStorage.setItem(KEY_SAVE, JSON.stringify(state));
    document.documentElement.dataset.motion = state.settings.reduceMotion ? "reduce" : "ok";
  }, [state]);

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) {
      window.location.hash = state.onboarded ? "/home" : "/welcome";
    }
    return () => window.removeEventListener("hashchange", onHash);
    // initial only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const api = useMemo<StoreValue>(() => {
    const navigate = (path: string) => {
      const next = path.startsWith("#") ? path : `#${path.startsWith("/") ? path : `/${path}`}`;
      if (window.location.hash === next) setRoute(parseRoute(next));
      else window.location.hash = next;
    };
    const showToast = (message: string) => setToast(message);
    return {
      state,
      route,
      toast,
      modal,
      navigate,
      goBack: () => navigate(parentPath(route)),
      showToast,
      openModal: (next) => setModal(next),
      closeModal: () => setModal(null),
      setOnboarded: () => setState((prev) => ({ ...prev, onboarded: true })),
      updateSettings: (patch) => setState((prev) => ({ ...prev, settings: { ...prev.settings, ...patch } })),
      resetDemo: () => {
        const fresh = createSeed();
        fresh.onboarded = true;
        setState(fresh);
        showToast("Демо-данные сброшены");
      },
      saveDraft: (draft) => setState((prev) => ({ ...prev, draft })),
      saveEntry: (entry) => {
        const next: DiaryEntry = { ...entry, id: uid("e"), createdAt: new Date().toISOString() };
        setState((prev) => ({ ...prev, entries: [next, ...prev.entries], draft: null }));
        showToast("Запись сохранена");
        navigate(`/diary/${next.id}`);
      },
      deleteEntry: (id) => {
        setState((prev) => ({ ...prev, entries: prev.entries.filter((item) => item.id !== id) }));
        showToast("Запись удалена");
        navigate("/diary");
      },
      linkPattern: (entryId, patternId) => {
        setState((prev) => ({
          ...prev,
          entries: prev.entries.map((item) => (item.id === entryId ? { ...item, patternId } : item)),
        }));
        showToast(patternId ? "Связь с паттерном сохранена" : "Связь снята");
      },
      addPattern: (pattern) => {
        const id = uid("pat");
        setState((prev) => ({ ...prev, patterns: [{ ...pattern, id }, ...prev.patterns] }));
        showToast("Паттерн добавлен");
        navigate(`/patterns/${id}`);
        return id;
      },
      setPatternStatus: (id, status) => {
        setState((prev) => ({
          ...prev,
          patterns: prev.patterns.map((item) => (item.id === id ? { ...item, status } : item)),
        }));
        showToast("Статус обновлён");
      },
      startPractice: (id) => {
        setState((prev) => ({ ...prev, startedPracticeId: id }));
        showToast("Практика начата");
      },
      completePractice: (id, usefulness, difficultyRating) => {
        setState((prev) => ({
          ...prev,
          startedPracticeId: prev.startedPracticeId === id ? null : prev.startedPracticeId,
          completions: [
            { id: uid("c"), practiceId: id, completedAt: new Date().toISOString(), usefulness, difficultyRating },
            ...prev.completions,
          ],
        }));
        showToast("Практика отмечена выполненной");
      },
      addSuggestionToPlan: (id) => {
        setState((prev) => ({
          ...prev,
          suggestions: prev.suggestions.map((item) => (item.id === id ? { ...item, added: true } : item)),
        }));
        showToast("Шаг добавлен в план");
      },
      commitAnalysis: (id) => {
        const used = analysesUsedToday(state);
        if (state.plan !== "premium") return "locked";
        if (used >= ANALYSES_LIMIT) return "limit";
        const entry = state.entries.find((item) => item.id === id);
        if (!entry) return "limit";
        const analysis = {
          fact: `Наблюдаемое здесь уже отделено от вывода. Более точная формулировка: ${entry.newView}`,
          patternNote: entry.patternId
            ? "Запись уже связана с гипотезой паттерна. Это не диагноз, а повод заметить повтор."
            : "Пока это отдельный эпизод. Повтор можно будет заметить, если похожая мысль вернётся.",
          step: entry.nextStep,
        };
        setState((prev) => {
          const currentUsed = prev.analysesDate === todayKey() ? prev.analysesUsed : 0;
          const exists = prev.suggestions.some((item) => item.fromEntryId === id);
          return {
            ...prev,
            analysesUsed: currentUsed + 1,
            analysesDate: todayKey(),
            entries: prev.entries.map((item) => (item.id === id ? { ...item, analysis } : item)),
            suggestions: exists
              ? prev.suggestions
              : [
                  {
                    id: uid("sug"),
                    title: "Небольшой шаг из разбора",
                    description: entry.nextStep,
                    minutes: 10,
                    fromEntryId: id,
                    added: false,
                  },
                  ...prev.suggestions,
                ],
          };
        });
        showToast("Разбор готов");
        return "ok";
      },
      setPlan: (plan) => {
        setState((prev) => ({ ...prev, plan }));
        showToast(plan === "premium" ? "Демо Premium включён" : "Демо-тариф: бесплатный");
      },
      markNotification: (id) =>
        setState((prev) => ({
          ...prev,
          notifications: prev.notifications.map((item) => (item.id === id ? { ...item, read: true } : item)),
        })),
      markAllNotifications: () =>
        setState((prev) => ({
          ...prev,
          notifications: prev.notifications.map((item) => ({ ...item, read: true })),
        })),
      markSummarySeen: () => setState((prev) => ({ ...prev, summarySeenDate: dayKey(new Date()) })),
    };
  }, [modal, route, state, toast]);

  return <StoreContext.Provider value={api}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreValue {
  const value = useContext(StoreContext);
  if (!value) throw new Error("Store missing");
  return value;
}
