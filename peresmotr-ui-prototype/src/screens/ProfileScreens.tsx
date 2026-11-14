import { Bell, ChevronRight, CreditCard, Settings as SettingsIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Logo } from "../components/Logo";
import { Card, PrimaryButton, SecondaryButton } from "../components/Ui";
import { formatShort, plural } from "../format";
import { useStore } from "../store";

export function ProfileScreen() {
  const { state, navigate } = useStore();
  const initial = (state.settings.name.trim()[0] || "П").toUpperCase();
  return (
    <div className="screen">
      <div className="stack">
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <div className="profile-btn" style={{ width: 56, height: 56, fontSize: 22 }}>{initial}</div>
          <div>
            <div className="eyebrow">Профиль</div>
            <h1 className="h1" style={{ fontSize: 28 }}>{state.settings.name || "Без имени"}</h1>
            <p className="small muted">{state.plan === "premium" ? "Premium · демо" : "Бесплатный тариф"}</p>
          </div>
        </div>
        <div className="stats">
          <div className="stat"><b>{state.entries.length}</b><span>{plural(state.entries.length, "запись", "записи", "записей")}</span></div>
          <div className="stat"><b>{state.completions.length}</b><span>{plural(state.completions.length, "практика", "практики", "практик")}</span></div>
        </div>
        <LinkRow icon={<SettingsIcon size={18} />} title="Настройки" onClick={() => navigate("/settings")} />
        <LinkRow icon={<Bell size={18} />} title="Уведомления" text={state.notifications.some((item) => !item.read) ? "Есть непрочитанные" : "Все прочитаны"} onClick={() => navigate("/notifications")} />
        <LinkRow icon={<CreditCard size={18} />} title="Информация о тарифе" onClick={() => navigate("/tariff")} />
        <LinkRow icon={<ChevronRight size={18} />} title="Тарифы" text="Бесплатный и Premium" onClick={() => navigate("/premium")} />
        <Card>
          <Logo variant="outline" height={36} />
          <p className="small muted mt12">ПЕРЕСМОТР хранит этот прототип только на устройстве. Боевое приложение и его данные не используются.</p>
        </Card>
      </div>
    </div>
  );
}

function LinkRow({ icon, title, text, onClick }: { icon: ReactNode; title: string; text?: string; onClick: () => void }) {
  return (
    <button className="row-link" type="button" onClick={onClick}>
      <span className="row-ico">{icon}</span>
      <span className="grow">
        <b>{title}</b>
        {text ? <small>{text}</small> : null}
      </span>
    </button>
  );
}

export function SettingsScreen() {
  const { state, updateSettings, resetDemo, openModal, closeModal, navigate, setPlan } = useStore();
  const { settings } = state;
  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">Демонстрация</div>
          <h1 className="h1">Настройки</h1>
        </div>
        <label className="field">
          <span className="label">Имя в профиле</span>
          <input className="input" value={settings.name} onChange={(event) => updateSettings({ name: event.target.value })} />
        </label>
        <Toggle title="Напоминание об итогах дня" text="Только локальная пометка, без настоящих уведомлений" on={settings.dailyReminder} onClick={() => updateSettings({ dailyReminder: !settings.dailyReminder })} />
        <Toggle title="Показывать уверенность в мыслях" text="Шкала в дневнике и на графике прогресса" on={settings.showConfidence} onClick={() => updateSettings({ showConfidence: !settings.showConfidence })} />
        <Toggle title="Компактные карточки" text="Чуть меньше воздуха в карточках" on={settings.compactCards} onClick={() => updateSettings({ compactCards: !settings.compactCards })} />
        <Toggle title="Меньше движения" text="Отключает анимации интерфейса" on={settings.reduceMotion} onClick={() => updateSettings({ reduceMotion: !settings.reduceMotion })} />
        <Card>
          <div className="eyebrow">Демо-тариф</div>
          <p className="small muted">Переключатель не проводит оплату. Он только показывает, как ведёт себя разбор записи.</p>
          <div className="btn-row mt12">
            <SecondaryButton onClick={() => setPlan("free")}>Бесплатный</SecondaryButton>
            <PrimaryButton onClick={() => setPlan("premium")}>Premium</PrimaryButton>
          </div>
        </Card>
        <button
          className="btn btn-danger btn-full"
          type="button"
          data-testid="reset-demo"
          onClick={() =>
            openModal({
              title: "Сбросить демо-данные?",
              body: "Вернутся исходные записи, паттерны и практики. То, что добавлено в этом браузере, исчезнет.",
              confirmLabel: "Сбросить",
              cancelLabel: "Отмена",
              tone: "danger",
              onConfirm: () => {
                closeModal();
                resetDemo();
                navigate("/home");
              },
            })
          }
        >
          Сбросить демо-данные
        </button>
      </div>
    </div>
  );
}

function Toggle({ title, text, on, onClick }: { title: string; text: string; on: boolean; onClick: () => void }) {
  return (
    <button className="toggle" type="button" aria-pressed={on} onClick={onClick}>
      <span>
        <b>{title}</b>
        <span className="small muted">{text}</span>
      </span>
      <span className={`switch ${on ? "on" : ""}`}><i /></span>
    </button>
  );
}

export function NotificationsScreen() {
  const { state, markNotification, markAllNotifications } = useStore();
  const unread = state.notifications.some((item) => !item.read);
  return (
    <div className="screen">
      <div className="stack">
        <div className="spread">
          <h1 className="h1">Уведомления</h1>
        </div>
        {unread ? <SecondaryButton onClick={markAllNotifications}>Прочитать все</SecondaryButton> : <p className="small muted">Непрочитанных нет.</p>}
        {state.notifications.map((item) => (
          <button key={item.id} className="list-card" type="button" onClick={() => markNotification(item.id)}>
            <div className="spread small muted">
              <span>{formatShort(item.at)}</span>
              {!item.read ? <span className="dot" aria-label="не прочитано" /> : <span>прочитано</span>}
            </div>
            <span className="thought">{item.title}</span>
            <span className="small muted">{item.body}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function TariffScreen() {
  const { state, navigate } = useStore();
  const premium = state.plan === "premium";
  return (
    <div className="screen">
      <div className="stack">
        <div>
          <div className="eyebrow">Сейчас</div>
          <h1 className="h1">{premium ? "Premium" : "Бесплатный"}</h1>
          <p className="lead mt12">
            {premium
              ? "Демо-доступ без оплаты. Разбор отдельных записей открыт в пределах пяти анализов в день."
              : "Дневник, паттерны, базовые практики и итоги дня доступны. Разбор отдельной записи — в Premium."}
          </p>
        </div>
        <Card>
          <div className="eyebrow">Что входит</div>
          <ul className="feature-list">
            {(premium
              ? ["Анализ записей", "Персональные задания", "Расширенная статистика", "Дневник и история сохраняются"]
              : ["Структурированный дневник", "Ручные паттерны", "Базовые практики", "Один обзор дня"]
            ).map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Card>
        <PrimaryButton full onClick={() => navigate("/premium")}>Смотреть тарифы</PrimaryButton>
      </div>
    </div>
  );
}
