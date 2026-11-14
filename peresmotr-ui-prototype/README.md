# ПЕРЕСМОТР — изолированный UI-прототип

Кликабельный прототип ребрендинга. Не подключён к боевому приложению, YDB, API и Telegram.

## Локально

```bash
cd peresmotr-ui-prototype
npm install
npm run dev
```

Откроется Vite. Маршруты — через hash (`#/home`, `#/diary`, …).

## Сборка для GitHub Pages

```bash
npm run build
```

Статика попадает в `../peresmotr/` (корень репозитория). Pages публикуется из ветки `main`, каталог `/`. После мержа прототип будет доступен по адресу:

`https://oleggrigorev131313.github.io/put-k-sebe/peresmotr/`

Корневой `index.html` эта сборка не меняет.

Данные прототипа лежат в `localStorage` под ключом `peresmotr-proto-v1`.
