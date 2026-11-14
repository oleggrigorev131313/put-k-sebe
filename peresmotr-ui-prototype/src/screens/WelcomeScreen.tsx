import { BookOpenText, Layers, Sparkles, TrendingUp } from "lucide-react";
import { Logo } from "../components/Logo";
import { PrimaryButton, TertiaryButton } from "../components/Ui";
import { useStore } from "../store";

const directions = [
  { title: "Дневник мыслей", text: "Ситуация, мысль, эмоция и следующий шаг", icon: <BookOpenText size={18} strokeWidth={1.6} /> },
  { title: "Паттерны мышления", text: "Гипотезы о привычных реакциях, не диагнозы", icon: <Sparkles size={18} strokeWidth={1.6} /> },
  { title: "Практики и упражнения", text: "Короткие действия, а не ритуалы", icon: <Layers size={18} strokeWidth={1.6} /> },
  { title: "Прогресс и изменения", text: "Только реальная активность", icon: <TrendingUp size={18} strokeWidth={1.6} /> },
];

export function WelcomeScreen() {
  const { navigate, setOnboarded } = useStore();
  return (
    <div className="welcome">
      <Mountains />
      <div className="welcome-body">
        <div className="logo-lockup">
          <Logo height={108} className="logo-float" />
          <h1 className="wordmark">ПЕРЕСМОТР</h1>
          <p className="slogan">УВИДЕТЬ ГЛУБЖЕ</p>
        </div>
        <p className="welcome-copy">
          Пространство, чтобы замечать автоматические мысли, отделять факты от интерпретаций и видеть повторяющиеся сценарии — спокойно и без оценки.
        </p>
        <div className="dirs">
          {directions.map((item) => (
            <div className="dir" key={item.title}>
              <span className="row-ico">{item.icon}</span>
              <span>
                <b>{item.title}</b>
                <span>{item.text}</span>
              </span>
            </div>
          ))}
        </div>
        <div className="welcome-foot">
          <PrimaryButton
            full
            data-testid="start-app"
            onClick={() => {
              setOnboarded();
              navigate("/home");
            }}
          >
            Начать
          </PrimaryButton>
          <TertiaryButton
            onClick={() => navigate("/account")}
          >
            У меня уже есть аккаунт
          </TertiaryButton>
        </div>
      </div>
    </div>
  );
}

export function AccountScreen() {
  const { navigate, setOnboarded, state, updateSettings } = useStore();
  return (
    <div className="screen">
      <div className="stack-lg">
        <Logo height={72} />
        <div>
          <div className="eyebrow">Локальная демонстрация</div>
          <h1 className="h1">Входа нет</h1>
          <p className="lead mt12">
            В этом прототипе нет сервера и аккаунта. Записи остаются в этом браузере и не отправляются никуда.
          </p>
        </div>
        <label className="field">
          <span className="label">Как к тебе обращаться в профиле</span>
          <input
            className="input"
            value={state.settings.name}
            onChange={(event) => updateSettings({ name: event.target.value })}
          />
        </label>
        <PrimaryButton
          full
          onClick={() => {
            setOnboarded();
            navigate("/home");
          }}
        >
          Продолжить локально
        </PrimaryButton>
      </div>
    </div>
  );
}

function Mountains() {
  return (
    <svg className="mountains" viewBox="0 0 400 220" preserveAspectRatio="none" aria-hidden="true">
      <path d="M0 120 C70 70 120 150 200 108 C280 66 330 130 400 90 L400 220 L0 220 Z" fill="#E7E1D4" />
      <path d="M0 150 C80 120 140 180 220 142 C300 104 350 156 400 128 L400 220 L0 220 Z" fill="#C9D0C4" opacity="0.85" />
      <path d="M0 176 C90 156 170 198 250 170 C320 148 360 176 400 160 L400 220 L0 220 Z" fill="#465752" opacity="0.16" />
    </svg>
  );
}
