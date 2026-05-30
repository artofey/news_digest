# News Digest Bot

Telegram-бот, который раз в день собирает новости за последние 24 часа и публикует дайджест в один или несколько каналов. Конфигурация каналов и тем хранится в Google Sheets — один запуск обслуживает все включённые каналы.

- **Источник новостей:** [Google News RSS](https://news.google.com) — без отдельного API-ключа
- **Конфигурация:** опубликованный CSV из Google Sheets (схема — [`docs/google-sheets-schema.md`](docs/google-sheets-schema.md))
- **Суммаризация:** Claude (Anthropic) или OpenAI — опционально, можно отключить
- **Деплой:** GitHub Actions по расписанию или локально через `python bot.py`

---

## Быстрый старт на GitHub Actions

Рекомендуемый способ — бот работает в облаке GitHub, сервер не нужен.

### 1. Форк или клон репозитория

```bash
git clone git@github.com:artofey/news_digest.git
cd news_digest
```

### 2. Настрой Google Sheets

1. Создай таблицу с колонками из [`docs/google-sheets-schema.md`](docs/google-sheets-schema.md).
2. **File → Share → Publish to web** → формат **Comma-separated values (.csv)**.
3. Скопируй URL — он понадобится как `GOOGLE_SHEET_CSV_URL`.

Пример готового CSV: [`docs/channels.example.csv`](docs/channels.example.csv).

Каждая строка — одна тема для одного канала. Несколько строк с одним `channel_id` объединяются в один дайджест.

### 3. Создай Telegram-бота и добавь в каналы

1. Напиши [@BotFather](https://t.me/BotFather) → `/newbot` → скопируй `TELEGRAM_BOT_TOKEN`.
2. Добавь **одного и того же бота** администратором в каждый канал с правом публиковать сообщения.
3. В таблице укажи `@channel_username` для публичного канала или числовой `channel_id` для приватного.

### 4. Добавь секреты и переменные в GitHub

Репозиторий → **Settings** → **Secrets and variables** → **Actions**

**Secrets:**

| Secret | Обязательно | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен от @BotFather |
| `ANTHROPIC_API_KEY` | ✅* | Ключ [Anthropic Console](https://console.anthropic.com/settings/keys) |
| `OPENAI_API_KEY` | ✅* | Если используешь OpenAI вместо Anthropic |

\* Нужен один из LLM-ключей, либо задай variable `USE_LLM=false` (см. ниже).

**Variables** (Settings → Secrets and variables → **Variables**):

| Variable | По умолчанию | Описание |
|---|---|---|
| `GOOGLE_SHEET_CSV_URL` | — | URL опубликованного CSV из Google Sheets |
| `USE_LLM` | `true` | `false` — только заголовки без AI (рекомендуется для бесплатного режима) |
| `LLM_PROVIDER` | `anthropic` | `anthropic` или `openai` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Модель Anthropic |

> `TELEGRAM_CHAT_ID` больше не нужен для продакшена — chat id каждого канала берётся из Google Sheets.

### 5. Запусти вручную для проверки

**Actions** → **📰 Daily News Digest** → **Run workflow**

Если всё настроено, в каждый включённый канал придёт один итоговый пост с дайджестом. Служебные сообщения («собираю новости…») в каналы не отправляются — только в логи GitHub Actions.

### 6. Автоматический запуск

Workflow запускается каждый день по cron. Чтобы изменить время, отредактируй `.github/workflows/digest.yml`:

```yaml
schedule:
  - cron: "0 5 * * *"   # 08:00 MSK (UTC+3)
```

> GitHub Actions cron может срабатывать с задержкой до 15 минут на free tier.

---

## Локальный запуск

```bash
pip install -r requirements.txt
cp env.example .env
# заполни .env (минимум GOOGLE_SHEET_CSV_URL и TELEGRAM_BOT_TOKEN)
python bot.py          # работает постоянно, шлёт в send_time из config.json
python bot.py --once   # один раз и выход (как в GitHub Actions)
```

### CLI-флаги для отладки

| Флаг | Описание |
|---|---|
| `--once` | Один запуск и выход (для cron / GitHub Actions) |
| `--channel <id>` | Обработать только один канал (`@username` или числовой id) |
| `--dry-run` | Вывести дайджест в консоль без отправки в Telegram |

Примеры:

```bash
python bot.py --once --dry-run
python bot.py --once --channel @my_channel --dry-run
python bot.py --once --channel @my_channel
```

### Fallback без Google Sheets

Если `GOOGLE_SHEET_CSV_URL` не задан, бот читает локальный `config.json` и отправляет в `TELEGRAM_CHAT_ID` — удобно для разработки:

```bash
# в .env: TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID, без GOOGLE_SHEET_CSV_URL
python bot.py --once
```

Для немедленной проверки локально: `"run_on_start": true` в `config.json`.

---

## config.json (fallback для локальной разработки)

| Поле | Описание |
|---|---|
| `topics` | Поисковые запросы для Google News |
| `news_language` | Локаль выдачи: `ru` или `en` |
| `digest_language` | Язык дайджеста (например, `русском`) |
| `send_time` | Время отправки при локальном запуске (`HH:MM`) |
| `max_articles_per_topic` | Лимит статей на тему |
| `run_on_start` | Запустить дайджест сразу при старте |
| `custom_instructions` | Доп. пожелания для AI-редактора |

В продакшене (GitHub Actions) используй Google Sheets вместо `config.json`.

---

## Структура

```
news_digest/
├── bot.py
├── config.json              # fallback для локальной разработки
├── env.example
├── requirements.txt
├── docs/
│   ├── google-sheets-schema.md
│   └── channels.example.csv
├── .github/workflows/digest.yml
└── README.md
```

---

## Стоимость

| Компонент | Цена |
|---|---|
| Google News RSS | бесплатно |
| Google Sheets (опубликованный CSV) | бесплатно |
| GitHub Actions | бесплатно (лимиты free tier) |
| Claude API | ~$0.003 за дайджест на канал → ~$0.09/мес при 1 канале и 1 запуске/день |

При `USE_LLM=false` LLM-ключ не нужен — бот отправляет упрощённый дайджест из заголовков и ссылок. Если LLM включён, но закончились кредиты, бот автоматически переключится на упрощённый формат.
