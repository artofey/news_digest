"""
📰 News Digest Bot
Каждое утро собирает новости за 24 часа и отправляет дайджест в Telegram.
Источник новостей: Google News RSS (бесплатно, без API-ключа).
"""

import os
import json
import time
import schedule
import requests
import feedparser
import argparse
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

load_dotenv()

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

_anthropic_client: Anthropic | None = None


def get_anthropic_client() -> Anthropic | None:
    global _anthropic_client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=api_key)
    return _anthropic_client


# ─── Получение новостей через Google News RSS ────────────────────────────────

NEWS_LOCALES = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "ru": {"hl": "ru", "gl": "RU", "ceid": "RU:ru"},
}


def _google_news_rss_url(query: str, language: str) -> str:
    locale = NEWS_LOCALES.get(language, NEWS_LOCALES["en"])
    encoded = quote(query)
    return (
        f"https://news.google.com/rss/search?q={encoded}"
        f"&hl={locale['hl']}&gl={locale['gl']}&ceid={locale['ceid']}"
    )


def fetch_news() -> list[dict]:
    topics: list[str] = config.get("topics", ["technology"])
    max_per_topic: int = config.get("max_articles_per_topic", 5)
    news_language: str = config.get("news_language", "en")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    all_articles: list[dict] = []
    seen_titles: set[str] = set()

    for topic in topics:
        url = _google_news_rss_url(topic, news_language)

        try:
            feed = feedparser.parse(url)

            if feed.bozo and not feed.entries:
                print(f"  ✗ [{topic}] — не удалось загрузить RSS")
                continue

            count = 0
            for entry in feed.entries:
                if count >= max_per_topic:
                    break

                title = entry.get("title", "").strip()
                if not title or title in seen_titles:
                    continue

                # Фильтр по дате (если есть)
                published = entry.get("published_parsed")
                if published:
                    try:
                        pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass  # если дата не парсится — берём статью

                seen_titles.add(title)
                all_articles.append({
                    "title": title,
                    "description": entry.get("summary", ""),
                    "url": entry.get("link", ""),
                    "source": {"name": entry.get("source", {}).get("title", "News")},
                    "_topic": topic,
                })
                count += 1

            print(f"  ✓ [{topic}] — {count} статей")

        except Exception as e:
            print(f"  ✗ [{topic}] — ошибка: {e}")

    return all_articles


# ─── Создание дайджеста ───────────────────────────────────────────────────────

def create_simple_digest(articles: list[dict]) -> str:
    """Дайджест без LLM — заголовки и ссылки, сгруппированные по темам."""
    topics: list[str] = config.get("topics", [])
    by_topic: dict[str, list[dict]] = {t: [] for t in topics}
    other: list[dict] = []

    for art in articles:
        topic = art.get("_topic", "")
        if topic in by_topic:
            by_topic[topic].append(art)
        else:
            other.append(art)

    lines: list[str] = []
    topic_emoji = {
        "artificial intelligence": "🤖",
        "technology startups": "🚀",
        "financial markets": "📈",
        "science discoveries": "🔬",
    }

    for topic in topics:
        items = by_topic.get(topic, [])
        if not items:
            continue
        emoji = topic_emoji.get(topic, "📌")
        lines.append(f"*{emoji} {topic.title()}*")
        for art in items:
            title = art["title"].replace("*", "").replace("_", "")
            url = art.get("url", "")
            source = art.get("source", {}).get("name", "")
            line = f"• {title}"
            if source:
                line += f" _({source})_"
            if url:
                line += f" — [читать]({url})"
            lines.append(line)
        lines.append("")

    if other:
        lines.append("*📌 Прочее*")
        for art in other:
            title = art["title"].replace("*", "").replace("_", "")
            url = art.get("url", "")
            if url:
                lines.append(f"• {title} — [читать]({url})")
            else:
                lines.append(f"• {title}")

    return "\n".join(lines).strip()


def _build_digest_prompt(articles: list[dict]) -> str:
    topics: list[str] = config.get("topics", [])
    digest_language: str = config.get("digest_language", "русском")
    custom_prompt: str = config.get("custom_instructions", "")

    articles_text = ""
    for i, art in enumerate(articles, 1):
        articles_text += (
            f"\n{i}. [Тема: {art['_topic']}]\n"
            f"   Заголовок: {art['title']}\n"
        )
        if art.get("description"):
            articles_text += f"   Описание: {art['description'][:300]}\n"
        articles_text += (
            f"   Источник: {art['source']['name']}\n"
            f"   Ссылка: {art['url']}\n"
        )

    return f"""Ты — редактор персонального новостного дайджеста.

Пользователь хочет получать новости по темам: {", ".join(topics)}.
{f"Дополнительные пожелания: {custom_prompt}" if custom_prompt else ""}

Вот список новостей за последние 24 часа:
{articles_text}

Составь дайджест на {digest_language} языке, строго следуя правилам:

1. Начни с 2-3 предложений — самое важное за день (overview).
2. Затем разбивка по темам. Для каждой темы — заголовок с эмодзи и 2-4 новости.
3. Каждая новость: 1-2 предложения своими словами + ссылка [читать](url).
4. Отбирай только реально важное, пропускай дубли и мелочи.
5. Форматирование для Telegram: *жирный* для заголовков тем, • для новостей.
6. Не используй HTML-теги, только Markdown.
7. Итого не больше 1800 символов."""


def create_digest_with_anthropic(articles: list[dict]) -> str:
    client = get_anthropic_client()
    if not client:
        raise ValueError("ANTHROPIC_API_KEY не задан")

    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": _build_digest_prompt(articles)}],
    )
    return message.content[0].text


def create_digest_with_openai(articles: list[dict]) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("Установи openai: pip install openai") from e

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY не задан")

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": _build_digest_prompt(articles)}],
    )
    return response.choices[0].message.content or ""


def create_digest(articles: list[dict]) -> str:
    if not articles:
        return "За последние 24 часа новостей по заданным темам не найдено."

    use_llm = os.getenv("USE_LLM", "true").lower() not in ("0", "false", "no")
    if not use_llm:
        print("ℹ️ USE_LLM=false — простой дайджест без AI")
        return create_simple_digest(articles)

    try:
        if LLM_PROVIDER == "openai":
            return create_digest_with_openai(articles)
        return create_digest_with_anthropic(articles)
    except APIStatusError as e:
        err_msg = str(e).lower()
        if "credit balance" in err_msg or "billing" in err_msg or e.status_code == 402:
            print("⚠️ Недостаточно кредитов Anthropic — отправляю простой дайджест")
        else:
            print(f"⚠️ Ошибка LLM ({e.status_code}): {e} — отправляю простой дайджест")
    except Exception as e:
        print(f"⚠️ LLM недоступен ({e}) — отправляю простой дайджест")

    notice = "_⚠️ AI-суммаризация недоступна (проверь баланс API). Ниже — заголовки новостей._\n\n"
    return notice + create_simple_digest(articles)


# ─── Отправка в Telegram ─────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы в .env")
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    max_len = 4000
    chunks = [text[i: i + max_len] for i in range(0, len(text), max_len)]

    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(api_url, json=payload, timeout=10)
            if not resp.ok:
                print(f"❌ Telegram API error: {resp.text}")
                return False
        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram: {e}")
            return False
        time.sleep(0.3)

    return True


# ─── Основной сценарий ────────────────────────────────────────────────────────

def run_digest():
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    print(f"\n[{now_str}] 🚀 Запуск дайджеста...")

    try:
        send_telegram("🔍 Собираю новости за последние 24 часа, подожди...")

        articles = fetch_news()
        print(f"Всего статей: {len(articles)}")

        digest_text = create_digest(articles)

        header = f"📰 *Дайджест новостей — {now_str}*\n\n"
        full_message = header + digest_text

        if send_telegram(full_message):
            print("✅ Дайджест отправлен!")
        else:
            print("❌ Не удалось отправить дайджест")

    except Exception as e:
        err = f"❌ Критическая ошибка дайджеста: {e}"
        print(err)
        send_telegram(err)


# ─── Запуск ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="News Digest Bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Запустить один раз и выйти (для GitHub Actions / cron)",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("📰 News Digest Bot")
    print(f"   Темы : {', '.join(config.get('topics', []))}")
    print("=" * 50)

    if args.once:
        print("⚡ Режим --once: запускаю дайджест...")
        run_digest()
        return

    send_time: str = config.get("send_time", "08:00")
    print(f"   Отправка: каждый день в {send_time}")
    print("=" * 50)

    schedule.every().day.at(send_time).do(run_digest)

    if config.get("run_on_start", False):
        print("⚡ run_on_start=true — запускаю немедленно...")
        run_digest()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()