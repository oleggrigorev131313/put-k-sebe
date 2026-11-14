import { useEffect } from "react";
import { AppShell, Header, tabFor } from "./components/Shell";
import { PrimaryButton, SecondaryButton } from "./components/Ui";
import { AccountScreen, WelcomeScreen } from "./screens/WelcomeScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { DiaryScreen, EntryScreen, WizardScreen } from "./screens/DiaryScreens";
import { PatternCreateScreen, PatternDetailScreen, PatternsScreen } from "./screens/PatternScreens";
import { PracticeDetailScreen, PracticesScreen } from "./screens/PracticeScreens";
import { PracticeHistoryScreen, ProgressScreen } from "./screens/ProgressScreens";
import { SummaryScreen } from "./screens/SummaryScreen";
import { PremiumScreen } from "./screens/PremiumScreen";
import { NotificationsScreen, ProfileScreen, SettingsScreen, TariffScreen } from "./screens/ProfileScreens";
import { useStore } from "./store";
import type { Route } from "./types";

function titleFor(route: Route): string {
  switch (route.name) {
    case "account":
      return "Аккаунт";
    case "wizard":
      return "Новая запись";
    case "entry":
      return "Запись";
    case "pattern-new":
      return "Новый паттерн";
    case "pattern":
      return "Паттерн";
    case "practice":
      return "Практика";
    case "practice-history":
      return "История практик";
    case "summary":
      return "Итоги дня";
    case "premium":
      return "Тарифы";
    case "profile":
      return "Профиль";
    case "settings":
      return "Настройки";
    case "notifications":
      return "Уведомления";
    case "tariff":
      return "Тариф";
    default:
      return "ПЕРЕСМОТР";
  }
}

function Screen() {
  const { route } = useStore();
  switch (route.name) {
    case "welcome":
      return <WelcomeScreen />;
    case "account":
      return <AccountScreen />;
    case "home":
      return <HomeScreen />;
    case "diary":
      return <DiaryScreen />;
    case "wizard":
      return <WizardScreen />;
    case "entry":
      return <EntryScreen id={route.id} />;
    case "patterns":
      return <PatternsScreen />;
    case "pattern-new":
      return <PatternCreateScreen />;
    case "pattern":
      return <PatternDetailScreen id={route.id} />;
    case "practices":
      return <PracticesScreen />;
    case "practice":
      return <PracticeDetailScreen id={route.id} />;
    case "progress":
      return <ProgressScreen />;
    case "practice-history":
      return <PracticeHistoryScreen />;
    case "summary":
      return <SummaryScreen when={route.when} />;
    case "premium":
      return <PremiumScreen />;
    case "profile":
      return <ProfileScreen />;
    case "settings":
      return <SettingsScreen />;
    case "notifications":
      return <NotificationsScreen />;
    case "tariff":
      return <TariffScreen />;
    default:
      return <HomeScreen />;
  }
}

export function App() {
  const store = useStore();
  const bare = store.route.name === "welcome";
  const mainTabs = ["home", "diary", "patterns", "practices", "progress"].includes(store.route.name);
  const initial = (store.state.settings.name.trim()[0] || "П").toUpperCase();

  useEffect(() => {
    document.querySelector(".main")?.scrollTo(0, 0);
  }, [store.route]);

  return (
    <AppShell
      showTabs={!bare && store.route.name !== "account"}
      active={tabFor(store.route)}
      onTab={store.navigate}
      header={
        <Header
          mode={bare ? "none" : mainTabs ? "main" : "inner"}
          title={titleFor(store.route)}
          initial={initial}
          onBack={store.goBack}
          onProfile={() => store.navigate("/profile")}
          onBrand={() => store.navigate("/home")}
        />
      }
      overlay={
        <>
          {store.modal ? (
            <div
              className="modal-back"
              role="presentation"
              onClick={() => {
                if (store.modal?.dismissable !== false) store.closeModal();
              }}
            >
              <div
                className="modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="modal-title"
                onClick={(event) => event.stopPropagation()}
              >
                <h2 id="modal-title">{store.modal.title}</h2>
                <p className="small">{store.modal.body}</p>
                <div className="btn-row mt16">
                  {store.modal.cancelLabel ? (
                    <SecondaryButton onClick={store.closeModal}>{store.modal.cancelLabel}</SecondaryButton>
                  ) : null}
                  {store.modal.tone === "danger" ? (
                    <button className="btn btn-danger" type="button" onClick={() => store.modal?.onConfirm?.()}>
                      {store.modal.confirmLabel || "Подтвердить"}
                    </button>
                  ) : (
                    <PrimaryButton onClick={() => (store.modal?.onConfirm ? store.modal.onConfirm() : store.closeModal())}>
                      {store.modal.confirmLabel || "Хорошо"}
                    </PrimaryButton>
                  )}
                </div>
              </div>
            </div>
          ) : null}
          {store.toast ? (
            <div className="toast" role="status">
              {store.toast}
            </div>
          ) : null}
        </>
      }
    >
      <Screen />
    </AppShell>
  );
}
