# News Digest Bot

Telegram-бот, который раз в день собирает новости за последние 24 часа и присылает персональный дайджест.

- **Источник новостей:** [Google News RSS](https://news.google.com) — без отдельного API-ключа
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

### 2. Настрой `config.json`

Отредактируй темы, язык и время. Для GitHub Actions поле `send_time` не используется — расписание задаётся в [`.github/workflows/digest.yml`](.github/workflows/digest.yml) (сейчас **08:00 MSK** = `0 5 * * *` UTC).

```json
{
  "topics": [
    "научные открытия исследования",
    "политика России",
    "политика Таиланда",
    "мировые конфликты война"
  ],
  "news_language": "ru",
  "digest_language": "русском",
  "max_articles_per_topic": 5,
  "custom_instructions": "..."
}
```

### 3. Создай Telegram-бота

1. Напиши [@BotFather](https://t.me/BotFather) → `/newbot` → скопируй `TELEGRAM_BOT_TOKEN`
2. Напиши боту `/start`
3. Открой `https://api.telegram.org/bot<TOKEN>/getUpdates` → найди `"chat":{"id":...}` — это `TELEGRAM_CHAT_ID`

### 4. Добавь секреты в GitHub

Репозиторий → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret | Обязательно | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен от @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | ID чата |
| `ANTHROPIC_API_KEY` | ✅* | Ключ [Anthropic Console](https://console.anthropic.com/settings/keys) |
| `OPENAI_API_KEY` | ✅* | Если используешь OpenAI вместо Anthropic |

\* Нужен один из LLM-ключей, либо добавь variable `USE_LLM=false` (см. ниже).

**Variables** (опционально, Settings → Secrets and variables → **Variables**):

| Variable | По умолчанию | Описание |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` или `openai` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Модель Anthropic |
| `USE_LLM` | `true` | `false` — только заголовки без AI |

### 5. Запусти вручную для проверки

**Actions** → **📰 Daily News Digest** → **Run workflow**

Если всё настроено, в Telegram придёт дайджест.

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
# заполни .env
python bot.py          # работает постоянно, шлёт в send_time
python bot.py --once   # один раз и выход
```

Для немедленной проверки локально: `"run_on_start": true` в `config.json`.

---

## config.json

| Поле | Описание |
|---|---|
| `topics` | Поисковые запросы для Google News |
| `news_language` | Локаль выдачи: `ru` или `en` |
| `digest_language` | Язык дайджеста (например, `русском`) |
| `send_time` | Время отправки при локальном запуске (`HH:MM`) |
| `max_articles_per_topic` | Лимит статей на тему |
| `run_on_start` | Запустить дайджест сразу при старте |
| `custom_instructions` | Доп. пожелания для AI-редактора |

---

## Структура

```
news_digest/
├── bot.py
├── config.json
├── env.example
├── requirements.txt
├── .github/workflows/digest.yml
└── README.md
```

---

## Стоимость

| Компонент | Цена |
|---|---|
| Google News RSS | бесплатно |
| GitHub Actions | бесплатно (лимиты free tier) |
| Claude API | ~$0.003 за дайджест → ~$0.09/мес при 1 запуске/день |

Если закончились кредиты Anthropic, бот автоматически отправит упрощённый дайджест — только заголовки и ссылки.
