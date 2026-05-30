"""
📰 News Digest Bot
Каждое утро собирает новости за 24 часа и отправляет дайджест в Telegram.
Источник новостей: Google News RSS (бесплатно, без API-ключа).
"""

import csv
import html
import io
import os
import json
import re
import time
import base64
import schedule
import requests
import feedparser
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TopicConfig:
    topic: str
    topic_label: str
    emoji: str | None = None
    news_language: str = "ru"
    max_articles: int = 5


@dataclass
class ChannelConfig:
    channel_id: str
    channel_name: str
    digest_language: str = "русском"
    custom_instructions: str = ""
    topics: list[TopicConfig] = field(default_factory=list)


_FALSE_VALUES = frozenset({"false", "0", "no", "n", "off"})


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() not in _FALSE_VALUES


def _parse_int(value: str | None, default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _parse_sheet_rows(rows: list[dict[str, str]]) -> list[ChannelConfig]:
    """Parse CSV rows and group them by channel_id."""
    channels: dict[str, ChannelConfig] = {}

    for row_num, row in enumerate(rows, start=2):
        normalized = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in row.items()
        }

        if not _parse_bool(normalized.get("enabled"), default=True):
            continue

        channel_id = normalized.get("channel_id", "")
        topic = normalized.get("topic", "")

        if not channel_id:
            print(f"  ⚠️ Строка {row_num}: пропуск — пустой channel_id")
            continue
        if not topic:
            print(f"  ⚠️ Строка {row_num} [{channel_id}]: пропуск — пустой topic")
            continue

        topic_cfg = TopicConfig(
            topic=topic,
            topic_label=normalized.get("topic_label") or topic,
            emoji=normalized.get("emoji") or None,
            news_language=normalized.get("news_language") or "ru",
            max_articles=_parse_int(normalized.get("max_articles"), 5),
        )

        if channel_id not in channels:
            channels[channel_id] = ChannelConfig(
                channel_id=channel_id,
                channel_name=normalized.get("channel_name") or channel_id,
                digest_language=normalized.get("digest_language") or "русском",
                custom_instructions=normalized.get("custom_instructions", ""),
                topics=[topic_cfg],
            )
        else:
            channels[channel_id].topics.append(topic_cfg)

    return list(channels.values())


def _load_channel_configs_from_csv(text: str) -> list[ChannelConfig]:
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        print("  ⚠️ CSV пуст или без заголовков")
        return []
    return _parse_sheet_rows(list(reader))


def _load_channel_configs_from_url(url: str) -> list[ChannelConfig]:
    print(f"📋 Загрузка конфигурации из Google Sheets...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except requests.RequestException as e:
        print(f"  ✗ Не удалось скачать CSV: {e}")
        return []

    configs = _load_channel_configs_from_csv(resp.text)
    print(f"  ✓ Загружено каналов: {len(configs)}")
    for ch in configs:
        print(f"    • {ch.channel_name} ({ch.channel_id}) — {len(ch.topics)} тем")
    return configs


def _load_channel_configs_from_json(path: str = "config.json") -> list[ChannelConfig]:
    """Fallback для локальной разработки: config.json + TELEGRAM_CHAT_ID."""
    print(f"📋 Загрузка конфигурации из {path} (fallback)...")
    channel_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not channel_id:
        print("  ⚠️ TELEGRAM_CHAT_ID не задан — укажи chat id для fallback-режима")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        print(f"  ✗ Не удалось прочитать {path}: {e}")
        return []

    topics = [
        TopicConfig(
            topic=topic,
            topic_label=topic,
            news_language=data.get("news_language", "ru"),
            max_articles=data.get("max_articles_per_topic", 5),
        )
        for topic in data.get("topics", [])
    ]

    if not topics:
        print("  ⚠️ В config.json нет topics")
        return []

    channel = ChannelConfig(
        channel_id=channel_id,
        channel_name=channel_id or "local-dev",
        digest_language=data.get("digest_language", "русском"),
        custom_instructions=data.get("custom_instructions", ""),
        topics=topics,
    )
    print(f"  ✓ Fallback: 1 канал, {len(topics)} тем")
    return [channel]


def load_channel_configs() -> list[ChannelConfig]:
    """Load channel configs from Google Sheets CSV or config.json fallback."""
    sheet_url = os.getenv("GOOGLE_SHEET_CSV_URL", "").strip()
    if sheet_url:
        return _load_channel_configs_from_url(sheet_url)
    return _load_channel_configs_from_json()


def _load_scheduler_config(path: str = "config.json") -> dict:
    """Локальные настройки планировщика (send_time, run_on_start) — только для режима daemon."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"send_time": "08:00", "run_on_start": False}


channel_configs: list[ChannelConfig] = load_channel_configs()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "anthropic").lower()

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


def _google_news_article_id(url: str) -> str | None:
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    if parsed.hostname != "news.google.com" or len(parts) < 2:
        return None
    if parts[-2] not in ("articles", "read"):
        return None
    article_id = parts[-1].split("?")[0]
    return article_id or None


def _try_offline_google_news_decode(url: str) -> str | None:
    article_id = _google_news_article_id(url)
    if not article_id:
        return None

    try:
        padded = article_id + "=" * (-len(article_id) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("latin1")
    except Exception:
        return None

    prefix = b"\x08\x13\x22".decode("latin1")
    if decoded.startswith(prefix):
        decoded = decoded[len(prefix):]

    suffix = b"\xd2\x01\x00".decode("latin1")
    if decoded.endswith(suffix):
        decoded = decoded[: -len(suffix)]

    length = decoded.encode("latin1")[0]
    if length >= 0x80:
        decoded = decoded[2: length + 1]
    else:
        decoded = decoded[1: length + 1]

    if decoded.startswith(("http://", "https://")):
        return decoded
    return None


def _get_google_news_decode_params(article_id: str) -> tuple[str, str] | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; NewsDigestBot/1.0; +https://github.com/artofey/news_digest)"
        ),
    }
    for page_url in (
        f"https://news.google.com/articles/{article_id}",
        f"https://news.google.com/rss/articles/{article_id}",
    ):
        try:
            resp = requests.get(page_url, headers=headers, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            continue

        sg_match = re.search(r'data-n-a-sg="([^"]+)"', resp.text)
        ts_match = re.search(r'data-n-a-ts="([^"]+)"', resp.text)
        if sg_match and ts_match:
            return sg_match.group(1), ts_match.group(1)

    return None


def _decode_google_news_with_signature(
    article_id: str, signature: str, timestamp: str
) -> str | None:
    payload = [
        "Fbv4je",
        (
            f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
            f'null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
            f'"{article_id}",{timestamp},"{signature}"]'
        ),
    ]
    try:
        resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": (
                    "Mozilla/5.0 (compatible; NewsDigestBot/1.0; "
                    "+https://github.com/artofey/news_digest)"
                ),
            },
            data=f"f.req={quote(json.dumps([[payload]]))}",
            timeout=15,
        )
        resp.raise_for_status()
        parsed_data = json.loads(resp.text.split("\n\n")[1])[:-2]
        decoded_url = json.loads(parsed_data[0][2])[1]
    except (requests.RequestException, json.JSONDecodeError, IndexError, TypeError, KeyError):
        return None

    if isinstance(decoded_url, str) and decoded_url.startswith(("http://", "https://")):
        return decoded_url
    return None


def _decode_google_news_url(url: str) -> str | None:
    direct = _try_offline_google_news_decode(url)
    if direct:
        return direct

    article_id = _google_news_article_id(url)
    if not article_id:
        return None

    params = _get_google_news_decode_params(article_id)
    if not params:
        return None

    signature, timestamp = params
    return _decode_google_news_with_signature(article_id, signature, timestamp)


def _resolve_google_news_urls(urls: list[str]) -> dict[str, str]:
    """Map Google News redirect URLs to shorter direct article URLs."""
    resolved: dict[str, str] = {}
    unique_urls = list(dict.fromkeys(urls))

    for index, url in enumerate(unique_urls):
        direct = _decode_google_news_url(url)
        if direct:
            resolved[url] = direct
        if index < len(unique_urls) - 1:
            time.sleep(0.2)

    return resolved


def fetch_news(channel: ChannelConfig) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    all_articles: list[dict] = []
    seen_titles: set[str] = set()

    for topic_cfg in channel.topics:
        url = _google_news_rss_url(topic_cfg.topic, topic_cfg.news_language)
        label = topic_cfg.topic_label

        try:
            feed = feedparser.parse(url)

            if feed.bozo and not feed.entries:
                print(f"  ✗ [{label}] — не удалось загрузить RSS")
                continue

            count = 0
            for entry in feed.entries:
                if count >= topic_cfg.max_articles:
                    break

                title = entry.get("title", "").strip()
                if not title or title in seen_titles:
                    continue

                published = entry.get("published_parsed")
                if published:
                    try:
                        pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass

                seen_titles.add(title)
                all_articles.append({
                    "title": title,
                    "description": entry.get("summary", ""),
                    "url": entry.get("link", ""),
                    "source": {"name": entry.get("source", {}).get("title", "News")},
                    "_topic": topic_cfg.topic,
                })
                count += 1

            print(f"  ✓ [{label}] — {count} статей")

        except Exception as e:
            print(f"  ✗ [{label}] — ошибка: {e}")

    if all_articles:
        redirect_urls = [a["url"] for a in all_articles if a.get("url")]
        resolved = _resolve_google_news_urls(redirect_urls)
        if resolved:
            for article in all_articles:
                direct = resolved.get(article.get("url", ""))
                if direct:
                    article["url"] = direct
            print(f"  ✓ Декодировано прямых ссылок: {len(resolved)}/{len(redirect_urls)}")

    return all_articles


# ─── Форматирование для Telegram ─────────────────────────────────────────────

def _escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _html_link(url: str, label: str = "читать") -> str:
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}">{_escape_html(label)}</a>'


def _split_telegram_message(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        block = paragraph if not current else f"\n\n{paragraph}"
        if len(current) + len(block) <= max_len:
            current += block
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= max_len:
            current = paragraph
            continue

        for line in paragraph.split("\n"):
            line_block = line if not current else f"\n{line}"
            if len(current) + len(line_block) <= max_len:
                current += line_block
            else:
                if current:
                    chunks.append(current)
                current = line[:max_len]

    if current:
        chunks.append(current)

    return chunks or [text[:max_len]]


def _html_to_plain(text: str) -> str:
    text = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2: \1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def _markdown_to_plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1: \2", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    return text


# ─── Создание дайджеста ───────────────────────────────────────────────────────

def create_simple_digest(articles: list[dict], channel: ChannelConfig) -> str:
    """Дайджест без LLM — заголовки и ссылки, сгруппированные по темам."""
    by_topic: dict[str, list[dict]] = {t.topic: [] for t in channel.topics}
    other: list[dict] = []

    for art in articles:
        topic = art.get("_topic", "")
        if topic in by_topic:
            by_topic[topic].append(art)
        else:
            other.append(art)

    lines: list[str] = []

    for topic_cfg in channel.topics:
        items = by_topic.get(topic_cfg.topic, [])
        if not items:
            continue
        emoji = topic_cfg.emoji or "📌"
        lines.append(f"<b>{emoji} {_escape_html(topic_cfg.topic_label)}</b>")
        for art in items:
            title = _escape_html(art["title"])
            url = art.get("url", "")
            source = art.get("source", {}).get("name", "")
            line = f"• {title}"
            if source:
                line += f" <i>({_escape_html(source)})</i>"
            if url:
                line += f" — {_html_link(url)}"
            lines.append(line)
        lines.append("")

    if other:
        lines.append("<b>📌 Прочее</b>")
        for art in other:
            title = _escape_html(art["title"])
            url = art.get("url", "")
            if url:
                lines.append(f"• {title} — {_html_link(url)}")
            else:
                lines.append(f"• {title}")

    return "\n".join(lines).strip()


def _build_digest_prompt(articles: list[dict], channel: ChannelConfig) -> str:
    topic_labels = [t.topic_label for t in channel.topics]
    digest_language = channel.digest_language
    custom_prompt = channel.custom_instructions

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

Пользователь хочет получать новости по темам: {", ".join(topic_labels)}.
{f"Дополнительные пожелания: {custom_prompt}" if custom_prompt else ""}

Вот список новостей за последние 24 часа:
{articles_text}

Составь дайджест на {digest_language} языке, строго следуя правилам:

1. Начни с 2-3 предложений — самое важное за день (overview).
2. Затем разбивка по темам. Для каждой темы — заголовок с эмодзи и 2-4 новости.
3. Каждая новость: 1-2 предложения своими словами + ссылка <a href="url">читать</a>.
4. Отбирай только реально важное, пропускай дубли и мелочи.
5. Форматирование для Telegram HTML: <b>жирный</b> для заголовков тем, • для новостей.
6. Ссылки только так: <a href="url">читать</a> — не выводи длинные URL в текст.
7. Не используй Markdown, только HTML-теги b, i, a.
8. Итого не больше 1800 символов."""


def create_digest_with_anthropic(articles: list[dict], channel: ChannelConfig) -> str:
    client = get_anthropic_client()
    if not client:
        raise ValueError("ANTHROPIC_API_KEY не задан")

    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": _build_digest_prompt(articles, channel)}],
    )
    return message.content[0].text


def create_digest_with_openai(articles: list[dict], channel: ChannelConfig) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("Установи openai: pip install openai") from e

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY не задан")

    model = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": _build_digest_prompt(articles, channel)}],
    )
    return response.choices[0].message.content or ""


def create_digest(articles: list[dict], channel: ChannelConfig) -> str:
    if not articles:
        return "За последние 24 часа новостей по заданным темам не найдено."

    use_llm = os.getenv("USE_LLM", "true").lower() not in ("0", "false", "no")
    if not use_llm:
        print("ℹ️ USE_LLM=false — простой дайджест без AI")
        return create_simple_digest(articles, channel)

    try:
        if LLM_PROVIDER == "openai":
            return create_digest_with_openai(articles, channel)
        return create_digest_with_anthropic(articles, channel)
    except APIStatusError as e:
        err_msg = str(e).lower()
        if "credit balance" in err_msg or "billing" in err_msg or e.status_code == 402:
            print("⚠️ Недостаточно кредитов Anthropic — отправляю простой дайджест")
        else:
            print(f"⚠️ Ошибка LLM ({e.status_code}): {e} — отправляю простой дайджест")
    except Exception as e:
        print(f"⚠️ LLM недоступен ({e}) — отправляю простой дайджест")

    notice = (
        "<i>⚠️ AI-суммаризация недоступна (проверь баланс API). "
        "Ниже — заголовки новостей.</i>\n\n"
    )
    return notice + create_simple_digest(articles, channel)


# ─── Отправка в Telegram ─────────────────────────────────────────────────────

def _send_telegram_chunk(
    api_url: str, chat_id: str, chunk: str, *, parse_mode: str | None
) -> bool:
    payload: dict = {
        "chat_id": chat_id,
        "text": chunk,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        resp = requests.post(api_url, json=payload, timeout=10)
    except Exception as e:
        print(f"  ❌ Ошибка отправки в Telegram: {e}")
        return False

    if resp.ok:
        return True

    print(f"  ❌ Telegram API error: {resp.text}")
    return False


def send_telegram(chat_id: str, text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        print("  ❌ TELEGRAM_BOT_TOKEN не задан в .env")
        return False
    if not chat_id:
        print("  ❌ chat_id канала пустой — пропуск отправки")
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _split_telegram_message(text)
    if len(chunks) > 1:
        print(
            f"  ℹ️ Сообщение разбито на {len(chunks)} части "
            f"({len(text)} символов, лимит Telegram — 4096)"
        )

    for chunk in chunks:
        if _send_telegram_chunk(api_url, chat_id, chunk, parse_mode="HTML"):
            time.sleep(0.3)
            continue

        print("  ⚠️ HTML не принят — повтор с Markdown")
        if _send_telegram_chunk(api_url, chat_id, chunk, parse_mode="Markdown"):
            time.sleep(0.3)
            continue

        print("  ⚠️ Markdown не принят — повтор plain text")
        plain = _html_to_plain(chunk)
        if plain != chunk:
            plain_chunk = plain
        else:
            plain_chunk = _markdown_to_plain(chunk)
        if not _send_telegram_chunk(api_url, chat_id, plain_chunk, parse_mode=None):
            return False
        time.sleep(0.3)

    return True


# ─── Основной сценарий ────────────────────────────────────────────────────────

def run_digest(
    channels: list[ChannelConfig] | None = None,
    *,
    dry_run: bool = False,
) -> int:
    """Собрать и отправить дайджест по каждому каналу. Возвращает число ошибок."""
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    targets = channels if channels is not None else load_channel_configs()

    print(f"\n[{now_str}] 🚀 Запуск дайджеста ({len(targets)} каналов)...")
    if dry_run:
        print("  ℹ️ dry-run: в Telegram ничего не отправляется")

    if not targets:
        print("❌ Нет каналов для обработки")
        return 1

    succeeded = 0
    failed = 0

    for channel in targets:
        label = f"{channel.channel_name} ({channel.channel_id})"
        print(f"\n── {label} ──")

        if not channel.channel_id.strip():
            print("  ⚠️ Пустой channel_id — канал пропущен")
            failed += 1
            continue
        if not channel.topics:
            print("  ⚠️ Нет тем — канал пропущен")
            failed += 1
            continue

        try:
            print("  🔍 Собираю новости за последние 24 часа...")
            articles = fetch_news(channel)
            print(f"  Всего статей: {len(articles)}")

            digest_text = create_digest(articles, channel)
            header = f"📰 <b>Дайджест новостей — {now_str}</b>\n\n"
            full_message = header + digest_text

            if dry_run:
                print("  --- dry-run: итоговый пост ---")
                print(full_message)
                print("  --- конец поста ---")
                succeeded += 1
                continue

            if send_telegram(channel.channel_id, full_message):
                print(f"  ✅ Дайджест отправлен в {channel.channel_id}")
                succeeded += 1
            else:
                print(f"  ❌ Не удалось отправить дайджест в {channel.channel_id}")
                failed += 1

        except Exception as e:
            print(f"  ❌ Ошибка канала {label}: {e}")
            failed += 1

    print(
        f"\n📊 Итог: {succeeded} успешно, {failed} с ошибкой "
        f"(всего {len(targets)} каналов)"
    )
    return failed


# ─── Запуск ───────────────────────────────────────────────────────────────────

def _filter_channels(
    channels: list[ChannelConfig], channel_id: str | None
) -> list[ChannelConfig]:
    if not channel_id:
        return channels
    needle = channel_id.strip()
    matched = [ch for ch in channels if ch.channel_id.strip() == needle]
    if not matched:
        print(f"❌ Канал не найден: {needle}")
    return matched


def main():
    parser = argparse.ArgumentParser(description="News Digest Bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Запустить один раз и выйти (для GitHub Actions / cron)",
    )
    parser.add_argument(
        "--channel",
        metavar="ID",
        help="Обработать только один канал (channel_id из таблицы)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Вывести дайджест в консоль без отправки в Telegram",
    )
    args = parser.parse_args()

    configs = load_channel_configs()
    targets = _filter_channels(configs, args.channel)

    print("=" * 50)
    print("📰 News Digest Bot")
    print(f"   Каналов: {len(configs)}")
    for ch in configs:
        labels = ", ".join(t.topic_label for t in ch.topics)
        print(f"   • {ch.channel_name}: {labels}")
    print("=" * 50)

    if args.once or args.dry_run or args.channel:
        if args.channel and not targets:
            raise SystemExit(1)
        print("⚡ Запуск дайджеста...")
        channels_arg = targets if args.channel else None
        errors = run_digest(channels_arg, dry_run=args.dry_run)
        raise SystemExit(1 if errors else 0)

    scheduler = _load_scheduler_config()
    send_time: str = scheduler.get("send_time", "08:00")
    print(f"   Отправка: каждый день в {send_time}")
    print("=" * 50)

    schedule.every().day.at(send_time).do(lambda: run_digest())

    if scheduler.get("run_on_start", False):
        print("⚡ run_on_start=true — запускаю немедленно...")
        run_digest(configs)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()