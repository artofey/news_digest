# Схема Google Sheets для мультиканального дайджеста

Бот читает одну опубликованную таблицу в формате CSV (`GOOGLE_SHEET_CSV_URL`). Каждая строка — одна тема для одного Telegram-канала. Строки с одинаковым `channel_id` объединяются в один дневной дайджест.

## Публикация таблицы

1. Создай Google Sheet с колонками из раздела ниже (первая строка — заголовки).
2. **File → Share → Publish to web** → формат **Comma-separated values (.csv)**.
3. Скопируй URL и укажи его в `GOOGLE_SHEET_CSV_URL` (локально в `.env`, в GitHub — в Variables).

Пример URL:

```text
https://docs.google.com/spreadsheets/d/e/2PACX-1vQ.../pub?output=csv
```

## Колонки

| Колонка | Обязательная | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `enabled` | да | boolean | `true` | Включена ли строка. Значения: `true` / `false` (регистр не важен). Строки с `false` игнорируются. |
| `channel_id` | да | string | — | Telegram-канал: `@username` для публичного или числовой id (например `-1001234567890`) для приватного. |
| `channel_name` | нет | string | `channel_id` | Человекочитаемое имя канала для логов и отладки. |
| `topic` | да | string | — | Поисковый запрос для [Google News RSS](https://news.google.com). |
| `topic_label` | нет | string | `topic` | Заголовок блока темы в дайджесте. |
| `emoji` | нет | string | — | Эмодзи перед блоком темы (один символ или короткая последовательность). |
| `news_language` | нет | enum | `ru` | Локаль выдачи Google News: `ru` или `en`. |
| `digest_language` | нет | string | `русском` | Язык текста дайджеста (используется в промпте LLM, например `русском`, `English`). |
| `max_articles` | нет | integer | `5` | Максимум статей на тему (рекомендуемый диапазон: 1–10). |
| `custom_instructions` | нет | string | — | Редакционная политика канала: тон, акценты, что включать/исключать. |

### Заголовок CSV (первая строка)

```csv
enabled,channel_id,channel_name,topic,topic_label,emoji,news_language,digest_language,max_articles,custom_instructions
```

Имена колонок должны совпадать **точно** (нижний регистр, без пробелов). Порядок колонок может быть любым, если заголовки корректны.

## Правила группировки

- **Один канал — несколько строк:** каждая строка добавляет отдельный блок тем в общий дайджест.
- **Поля уровня канала** (`channel_name`, `news_language`, `digest_language`, `custom_instructions`): для одного `channel_id` указывай одинаковые значения во всех строках. Загрузчик берёт их из первой включённой строки канала.
- **Поля уровня темы** (`topic`, `topic_label`, `emoji`, `max_articles`): могут отличаться в каждой строке.

## Валидация

Строка **пропускается** (с записью в лог), если:

- `enabled` = `false`;
- пустой `channel_id`;
- пустой `topic`.

Ошибка одного канала **не останавливает** обработку остальных.

## Telegram

- Один бот (`TELEGRAM_BOT_TOKEN`) добавляется администратором во все каналы с правом публиковать сообщения.
- Публичный канал: `@my_channel`.
- Приватный канал: числовой id (узнать через [@getidsbot](https://t.me/getidsbot) или `getUpdates`, когда бот уже админ).

## Пример файла

Готовый пример с тремя каналами: [`channels.example.csv`](channels.example.csv).

### Краткий фрагмент

| enabled | channel_id | channel_name | topic | topic_label | emoji | news_language | digest_language | max_articles | custom_instructions |
|---|---|---|---|---|---|---|---|---|---|
| true | @science_ru | Наука RU | научные открытия | Наука | 🔬 | ru | русском | 5 | Акцент на прорывные исследования |
| true | @science_ru | Наука RU | политика России | Политика РФ | 🏛️ | ru | русском | 5 | Акцент на прорывные исследования |
| true | @tech_daily_en | Tech Daily | artificial intelligence | AI | 🤖 | en | English | 3 | Concise, no hype |
| false | @old_channel | Старый канал | ... | ... | | ru | русском | 5 | Отключён |

## Миграция с `config.json`

Старый локальный `config.json` (один канал, список `topics`) остаётся fallback для разработки. В Google Sheets каждый элемент бывшего массива `topics` становится отдельной строкой с общим `channel_id`:

| Было в config.json | Стало в Sheets |
|---|---|
| `topics[]` | несколько строк с одним `channel_id`, разными `topic` |
| `news_language` | колонка `news_language` |
| `digest_language` | колонка `digest_language` |
| `max_articles_per_topic` | колонка `max_articles` |
| `custom_instructions` | колонка `custom_instructions` |

Поле `send_time` в таблице не используется — расписание задаётся в GitHub Actions (`.github/workflows/digest.yml`).
