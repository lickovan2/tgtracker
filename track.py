"""Снимает публичные страницы и файлы Telegram в папку data/, чтобы изменения были видны в git.

Запуск: python track.py web|translations|tl|clients [--limit N]

Каждый режим пишет только в свою папку. Страница, которую не удалось получить, сохраняет
прежнюю версию: сбой сети не должен выглядеть как удаление.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import logging
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import aiohttp

ROOT = Path(__file__).resolve().parent
DATA = Path(__import__("os").environ.get("TRACK_DATA", ROOT / "data"))

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
CONCURRENCY = 12
RETRIES = 4
MAX_PAGES_PER_HOST = 20_000
MAX_PATH_DEPTH = 6
# Файлы не удаляем, если получить удалось меньше этой доли страниц: похоже на сбой, а не на удаление.
MIN_SUCCESS_FOR_DELETE = 0.97

WEB_SEEDS = [
    "https://telegram.org/", "https://core.telegram.org/", "https://desktop.telegram.org/",
    "https://instantview.telegram.org/", "https://promote.telegram.org/", "https://contest.com/",
    "https://webk.telegram.org/", "https://webz.telegram.org/", "https://translations.telegram.org/",
]
WEB_HOSTS = {urlsplit(seed).hostname for seed in WEB_SEEDS}
# Страницы переводов снимает отдельный режим, а здесь нужна только главная.
CRAWL_HOSTS = WEB_HOSTS - {"translations.telegram.org"}
# /moderation и /safety показывают живые счётчики и меняются при каждом запросе.
SKIP_PATH = re.compile(
    r"^/(dl|file|apps|widget|js/widget|moderation|safety)(/|$)|\.(apk|zip|dmg|exe|tar|gz|mp4|webm|pdf)$", re.I,
)

# Работы участников конкурсов и профили: десятки тысяч страниц о людях, а не о Telegram.
# Документация классов TDLib генерируется из td_api.tl, которую и так снимает режим tl.
SKIP_BY_HOST = {
    "instantview.telegram.org": re.compile(r"^/(contest|templates|my)/"),
    "contest.com": re.compile(r"^/user/|/entry\d+/?$"),
    "core.telegram.org": re.compile(r"^/tdlib/docs/"),
}

TL_SOURCES = {
    "td_api.tl": "https://raw.githubusercontent.com/tdlib/td/master/td/generate/scheme/td_api.tl",
    "telegram_api.tl": "https://raw.githubusercontent.com/tdlib/td/master/td/generate/scheme/telegram_api.tl",
    "tdesktop_api.tl": "https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/Telegram/SourceFiles/mtproto/scheme/api.tl",
}

TRANSLATION_PLATFORMS = ("android", "android_x", "ios", "macos", "tdesktop", "unigram", "weba", "webk")
TRANSLATIONS_URL = "https://translations.telegram.org"

# То, что меняется при каждом запросе и ничего не значит.
VOLATILE = [
    (re.compile(r"<!-- page generated in [^>]*-->"), ""),
    (re.compile(r"\?hash=[a-f0-9]+"), "?hash=X"),
    (re.compile(r'"nonce":"[^"]+"'), '"nonce":"X"'),
    (re.compile(r'"tonRate":"[^"]+"'), '"tonRate":"X"'),
    (re.compile(r'data-cdata="[^"]+"'), 'data-cdata="X"'),
    (re.compile(r"passport_ssid=[a-z0-9_]+"), "passport_ssid=X"),
    (re.compile(r"apk\?token=[^\"'&\s]+"), "apk?token=X"),
    (re.compile(r"(cdn\d*\.(?:telesco\.pe|cdn-telegram\.org)/file/[^\"'\s?]+)\?token=[^\"'\s]+"), r"\1"),
]
LINK_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)""", re.I)
ASSET_RE = re.compile(r"""<(?:link|script|img|source)\b[^>]*?(?:href|src)\s*=\s*["']([^"'#]+)""", re.I)
TEXT_TYPES = ("text/", "javascript", "json", "xml", "svg")

logger = logging.getLogger("track")


def normalize_html(text: str) -> str:
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    # Каждый тег на своей строке, иначе дифф минифицированной страницы нечитаем.
    return re.sub(r">\s*<", ">\n<", text).strip() + "\n"


def page_path(root: Path, url: str, is_html: bool = True) -> Path:
    parts = urlsplit(url)
    path = unquote(parts.path).strip("/")
    segments = [re.sub(r'[<>:"\\|?*\x00-\x1f]', "_", s) for s in path.split("/") if s not in ("", ".", "..")]
    name = "/".join(segments) or "index"
    if is_html and not name.endswith((".html", ".htm")):
        name += ".html"
    return root / parts.hostname / name


def is_link_trap(path: str) -> bool:
    """Относительная ссылка вроде t.me/x на странице порождает бесконечные /a/t.me/t.me/t.me/x."""
    segments = [s for s in path.split("/") if s]
    return len(segments) > MAX_PATH_DEPTH or "t.me" in segments or len(set(segments)) < len(segments)


def links_of(page: str, base: str) -> tuple[set[str], set[str]]:
    """(страницы для обхода, ресурсы страницы) с абсолютными адресами без якорей."""
    pages, assets = set(), set()
    for raw in ASSET_RE.findall(page):
        url = urljoin(base, html.unescape(raw).strip())
        if urlsplit(url).scheme == "https":
            assets.add(url)
    for raw in LINK_RE.findall(page):
        url = urljoin(base, html.unescape(raw).strip())
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in CRAWL_HOSTS or SKIP_PATH.search(parts.path):
            continue
        host_skip = SKIP_BY_HOST.get(parts.hostname)
        if host_skip and host_skip.search(parts.path):
            continue
        if is_link_trap(parts.path):
            continue
        # Параметры дают копии страниц (?setln=, ?layer=), снимаем только сам адрес.
        pages.add(f"https://{parts.hostname}{parts.path or '/'}")
    return pages, assets


def write_if_changed(path: Path, content: str | bytes) -> bool:
    """Адрес может оказаться несохраняемым (файл и папка с одним именем): пропускаем его, а не весь обход."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        if path.is_file() and path.read_bytes() == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as e:
        logger.warning("не сохранён %s: %s", path, e)
        return False
    return True


class Fetcher:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.gate = asyncio.Semaphore(CONCURRENCY)

    async def get(self, url: str, **kwargs) -> tuple[int, str, bytes]:
        """(статус, тип, тело). Сеть, 429 и 5xx повторяются; итоговый сбой даёт статус 0."""
        delay = 2.0
        for attempt in range(RETRIES):
            try:
                async with self.gate, self.session.get(url, allow_redirects=True, **kwargs) as resp:
                    body = await resp.read()
                    if resp.status != 429 and resp.status < 500:
                        return resp.status, resp.headers.get("Content-Type", ""), body
                    retry_after = resp.headers.get("Retry-After", "")
                    delay = max(delay, float(retry_after)) if retry_after.isdigit() else delay
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug("%s: %s", url, e)
            if attempt + 1 < RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
        return 0, "", b""


async def track_web(fetcher: Fetcher, limit: int | None) -> None:
    root, static_root = DATA / "web", DATA / "web_static"
    queue, seen, assets = list(WEB_SEEDS), set(WEB_SEEDS), set()
    written: set[Path] = set()
    host_counts: dict[str, int] = {}
    fetched = failed = 0

    async def visit(url: str) -> set[str]:
        nonlocal fetched, failed
        status, ctype, body = await fetcher.get(url)
        if status == 0 or status >= 500:
            failed += 1
            return set()
        fetched += 1
        if status != 200 or "html" not in ctype:
            return set()
        text = body.decode("utf-8", "replace")
        path = page_path(root, url)
        write_if_changed(path, normalize_html(text))
        written.add(path)
        pages, page_assets = links_of(text, url)
        assets.update(page_assets)
        return pages

    while queue:
        batch, queue = queue[:CONCURRENCY * 4], queue[CONCURRENCY * 4:]
        for pages in await asyncio.gather(*(visit(url) for url in batch)):
            for page in pages:
                host = urlsplit(page).hostname
                if page in seen or host_counts.get(host, 0) >= MAX_PAGES_PER_HOST:
                    continue
                if limit and len(seen) >= limit:
                    continue
                seen.add(page)
                host_counts[host] = host_counts.get(host, 0) + 1
                queue.append(page)
        logger.info("web: снято %d, ошибок %d, в очереди %d", fetched, failed, len(queue))

    static_written = await track_static(fetcher, assets, static_root, limit)
    if limit is None and fetched and fetched / (fetched + failed) >= MIN_SUCCESS_FOR_DELETE:
        remove_missing(root, written)
        remove_missing(static_root, static_written)
    elif limit is None:
        logger.warning("web: много ошибок (%d из %d), удалённые страницы не трогаю", failed, fetched + failed)


async def track_static(fetcher: Fetcher, assets: set[str], root: Path, limit: int | None) -> set[Path]:
    """CSS и JS сохраняются как есть, картинки и остальное только хэшем."""
    written: set[Path] = set()
    selected = sorted(asset for asset in assets if urlsplit(asset).hostname in WEB_HOSTS)
    if limit:
        selected = selected[:limit]

    async def save(url: str) -> None:
        status, ctype, body = await fetcher.get(url)
        if status != 200:
            return
        is_text = any(kind in ctype for kind in TEXT_TYPES)
        path = page_path(root, url, is_html=False)
        if is_text:
            write_if_changed(path, normalize_html(body.decode("utf-8", "replace")) if "html" in ctype else body)
        else:
            path = path.with_name(path.name + ".sha256")
            write_if_changed(path, hashlib.sha256(body).hexdigest() + "\n")
        written.add(path)

    await asyncio.gather(*(save(url) for url in selected))
    logger.info("статика: %d файлов", len(written))
    return written


def remove_missing(root: Path, keep: set[Path]) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path not in keep:
            path.unlink()


async def track_tl(fetcher: Fetcher) -> None:
    for name, url in TL_SOURCES.items():
        status, _ctype, body = await fetcher.get(url)
        if status == 200:
            write_if_changed(DATA / "tl" / name, body)
        else:
            logger.warning("tl %s: статус %s", name, status)


def parse_translation_rows(page: str) -> dict[str, list[str]]:
    """{ключ: тексты}. У ключей с множественным числом текстов несколько."""
    rows: dict[str, list[str]] = {}
    for block in page.split('class="tr-key-row"')[1:]:
        key = re.search(r'data-key="([^"]+)"', block)
        default = re.search(r'class="tr-value-default">(.*?)<div class="tr-value-key"', block, re.S)
        if not key or not default:
            continue
        values = re.findall(r'<span class="value">(.*?)</span>', default.group(1), re.S)
        rows[html.unescape(key.group(1))] = [html.unescape(re.sub(r"<[^>]+>", "", v)) for v in values]
    return rows


async def fetch_translation_section(fetcher: Fetcher, base: str) -> dict[str, list[str]] | None:
    rows: dict[str, list[str]] = {}
    offset = 0
    while True:
        url = f"{base}?offset={offset}" if offset else base
        status, _ctype, body = await fetcher.get(url)
        if status != 200:
            return None
        page = body.decode("utf-8", "replace")
        found = parse_translation_rows(page)
        before = len(rows)
        rows.update(found)
        more = re.search(r'class="tr-load-more load-more" data-offset="(\d+)"', page)
        if not more or len(rows) == before:
            return rows
        offset = int(more.group(1))


async def track_translations(fetcher: Fetcher, limit: int | None) -> None:
    root = DATA / "translations"

    async def platform(name: str) -> None:
        status, _ctype, body = await fetcher.get(f"{TRANSLATIONS_URL}/en/{name}/")
        if status != 200:
            logger.warning("переводы %s: статус %s", name, status)
            return
        sections = sorted(set(re.findall(rf'href="/en/{name}/([a-z_]+)/"', body.decode("utf-8", "replace"))))
        if limit:
            sections = sections[:limit]
        total = 0
        for section in sections:
            rows = await fetch_translation_section(fetcher, f"{TRANSLATIONS_URL}/en/{name}/{section}/")
            if rows is None:
                logger.warning("переводы %s/%s не получены", name, section)
                continue
            total += len(rows)
            write_if_changed(root / name / f"{section}.json", json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        logger.info("переводы %s: разделов %d, ключей %d", name, len(sections), total)

    await asyncio.gather(*(platform(name) for name in TRANSLATION_PLATFORMS))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("web", "translations", "tl", "clients"))
    parser.add_argument("--limit", type=int, help="для проверки: ограничить объём и не удалять файлы")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        fetcher = Fetcher(session)
        if args.mode == "web":
            await track_web(fetcher, args.limit)
        elif args.mode == "translations":
            await track_translations(fetcher, args.limit)
        elif args.mode == "tl":
            await track_tl(fetcher)
        else:
            import clients
            await clients.track(session, DATA / "client")


if __name__ == "__main__":
    asyncio.run(main())
