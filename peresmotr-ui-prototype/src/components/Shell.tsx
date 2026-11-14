import { ChevronLeft, Home, Layers, PencilLine, TrendingUp } from "lucide-react";
import type { ReactNode } from "react";
import type { Route, TabId } from "../types";
import { Logo } from "./Logo";

function PatternsIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="9" cy="12" r="5.25" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="15" cy="12" r="5.25" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

const TABS: { id: TabId; label: string; path: string; icon: ReactNode }[] = [
  { id: "home", label: "Главная", path: "/home", icon: <Home size={20} strokeWidth={1.6} /> },
  { id: "diary", label: "Дневник", path: "/diary", icon: <PencilLine size={20} strokeWidth={1.6} /> },
  { id: "patterns", label: "Паттерны", path: "/patterns", icon: <PatternsIcon /> },
  { id: "progress", label: "Прогресс", path: "/progress", icon: <TrendingUp size={20} strokeWidth={1.6} /> },
  { id: "practices", label: "Практики", path: "/practices", icon: <Layers size={20} strokeWidth={1.6} /> },
];

export function tabFor(route: Route): TabId | null {
  switch (route.name) {
    case "home":
    case "summary":
      return "home";
    case "diary":
    case "wizard":
    case "entry":
      return "diary";
    case "patterns":
    case "pattern":
    case "pattern-new":
      return "patterns";
    case "progress":
    case "practice-history":
      return "progress";
    case "practices":
    case "practice":
      return "practices";
    default:
      return null;
  }
}

export function parentPath(route: Route): string {
  switch (route.name) {
    case "wizard":
    case "entry":
      return "/diary";
    case "pattern":
    case "pattern-new":
      return "/patterns";
    case "practice":
      return "/practices";
    case "practice-history":
      return "/progress";
    case "summary":
      return "/home";
    case "settings":
    case "notifications":
    case "tariff":
    case "premium":
      return "/profile";
    case "profile":
      return "/home";
    case "account":
      return "/welcome";
    default:
      return "/home";
  }
}

export function AppShell({
  children,
  header,
  showTabs,
  active,
  onTab,
  overlay,
}: {
  children: ReactNode;
  header: ReactNode;
  showTabs: boolean;
  active: TabId | null;
  onTab: (path: string) => void;
  overlay?: ReactNode;
}) {
  return (
    <div className="frame">
      <div className="shell">
        {header}
        <main className="main">{children}</main>
        {showTabs ? <BottomNavigation active={active} onTab={onTab} /> : null}
        {overlay}
      </div>
    </div>
  );
}

export function Header({
  mode,
  title,
  initial,
  onBack,
  onProfile,
  onBrand,
}: {
  mode: "main" | "inner" | "none";
  title?: string;
  initial: string;
  onBack?: () => void;
  onProfile: () => void;
  onBrand: () => void;
}) {
  if (mode === "none") return null;
  if (mode === "inner") {
    return (
      <header className="header">
        <button className="icon-btn" type="button" aria-label="Назад" onClick={onBack}>
          <ChevronLeft size={20} strokeWidth={1.6} />
        </button>
        <div className="header-title">{title}</div>
        <button className="profile-btn" type="button" aria-label="Профиль" onClick={onProfile}>
          {initial}
        </button>
      </header>
    );
  }
  return (
    <header className="header">
      <button className="brand" type="button" onClick={onBrand} aria-label="На главную">
        <Logo height={28} />
        <span className="brand-name">ПЕРЕСМОТР</span>
      </button>
      <button className="profile-btn" type="button" aria-label="Профиль" onClick={onProfile}>
        {initial}
      </button>
    </header>
  );
}

export function BottomNavigation({ active, onTab }: { active: TabId | null; onTab: (path: string) => void }) {
  return (
    <nav className="tabbar" aria-label="Разделы">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          className={`tab ${active === tab.id ? "active" : ""}`}
          type="button"
          aria-current={active === tab.id ? "page" : undefined}
          data-testid={`tab-${tab.id}`}
          onClick={() => onTab(tab.path)}
        >
          <span className="tab-ico">{tab.icon}</span>
          <span>{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}
