import { useState } from "react";
import { PRICES } from "../data/demo";
import { Logo } from "../components/Logo";
import { Card, PrimaryButton, SecondaryButton } from "../components/Ui";
import { useStore } from "../store";

const FREE = [
  "Структурированный дневник мыслей",
  "Работа с ситуацией, эмоциями и убеждениями",
  "Проверка фактов",
  "Ручное ведение паттернов",
  "Базовые практики",
  "История и хранение записей",
];

const PREMIUM = [
  "Анализ записей и истории",
  "Выявление повторяющихся паттернов",
  "Персональные задания",
  "Эмоциональные закономерности",
  "Регулярные сводки",
  "Расширенная статистика",
];

export function PremiumScreen() {
  const { state, openModal, closeModal, setPlan, showToast } = useStore();
  const [busy, setBusy] = useState<string | null>(null);

  function pay(id: string, title: string, price: string) {
    setBusy(id);
    window.setTimeout(() => {
      setBusy(null);
      openModal({
        title: "Это демонстрация",
        body: `${title} · ${price}. Оплата не выполняется, карта не нужна, списания нет. Можно только посмотреть, как выглядит Premium в прототипе.`,
        confirmLabel: state.plan === "premium" ? "Понятно" : "Включить демо Premium",
        cancelLabel: "Закрыть",
        onConfirm: () => {
          closeModal();
          if (state.plan !== "premium") {
            setPlan("premium");
          } else {
            showToast("Оплата в прототипе не запускается");
          }
        },
      });
    }, 500);
  }

  return (
    <div className="screen">
      <div className="stack">
        <section className="dark-hero">
          <div className="milk-plate">
            <Logo height={78} />
          </div>
          <div className="wordmark" style={{ textAlign: "center", fontSize: 18 }}>ПЕРЕСМОТР</div>
          <p className="mt12" style={{ textAlign: "center", color: "#d5dbd6" }}>
            Premium — персональный аналитик. Тот же спокойный инструмент, с разбором записей и гипотезами о повторах.
          </p>
        </section>
        <Card>
          <div className="spread">
            <div className="eyebrow">Бесплатный</div>
            <span className="pill">0 ₽</span>
          </div>
          <h2 className="h2">Самостоятельная работа</h2>
          <ul className="feature-list mt12">
            {FREE.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Card>
        <Card>
          <div className="spread">
            <div className="eyebrow">Premium</div>
            <span className={`pill ${state.plan === "premium" ? "ok" : ""}`}>{state.plan === "premium" ? "Демо включено" : "Не оплачен"}</span>
          </div>
          <h2 className="h2">Персональный аналитик</h2>
          <ul className="feature-list mt12">
            {PREMIUM.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Card>
        <PriceCard
          title={PRICES.month.title}
          price={PRICES.month.price}
          note={PRICES.month.note}
          loading={busy === "month"}
          onClick={() => pay("month", PRICES.month.title, PRICES.month.price)}
        />
        <PriceCard
          title={PRICES.quarter.title}
          price={PRICES.quarter.price}
          note={PRICES.quarter.note}
          loading={busy === "quarter"}
          featured
          onClick={() => pay("quarter", PRICES.quarter.title, PRICES.quarter.price)}
        />
        <p className="hint">Цены как в текущем приложении. Покупка разовая, автопродления нет. По окончании срока дневник остаётся.</p>
        {state.plan === "premium" ? (
          <SecondaryButton full onClick={() => setPlan("free")}>Выключить демо Premium</SecondaryButton>
        ) : null}
      </div>
    </div>
  );
}

function PriceCard({
  title,
  price,
  note,
  onClick,
  loading,
  featured = false,
}: {
  title: string;
  price: string;
  note: string;
  onClick: () => void;
  loading: boolean;
  featured?: boolean;
}) {
  return (
    <Card>
      <div className="eyebrow">{featured ? "Три месяца" : "Один месяц"}</div>
      <h2 className="h2">{title}</h2>
      <div className="price">{price}</div>
      <p className="small muted">{note}</p>
      <div className="mt16">
        <PrimaryButton full loading={loading} onClick={onClick} data-testid={`pay-${title}`}>
          Посмотреть оплату
        </PrimaryButton>
      </div>
    </Card>
  );
}
