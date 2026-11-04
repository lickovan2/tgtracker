"""Сценарии трекера с подменённой сетью: сбои и блокировки не должны выглядеть как изменения сайта.

Запуск: python test_scenarios.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

os.environ["TRACK_DATA"] = tempfile.mkdtemp()
import clients
import track

PAGE = '<html><!-- page generated in 1ms --><a href="/tour">t</a><link rel="stylesheet" href="/a.css">текст %s</html>'


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    async def get(self, url, **_):
        return self.pages.get(url, (404, "text/html", b"nope"))


def fresh_data():
    track.DATA = Path(tempfile.mkdtemp())
    return track.DATA


def site(text="v1", extra=None):
    pages = {seed: (200, "text/html", (PAGE % text).encode()) for seed in track.WEB_SEEDS}
    pages["https://telegram.org/tour"] = (200, "text/html", (PAGE % f"тур {text}").encode())
    pages["https://telegram.org/a.css"] = (200, "text/css", b"body{}")
    pages.update(extra or {})
    return pages


def crawl(pages, limit=None):
    asyncio.run(track.track_web(FakeFetcher(pages), limit))


def files(root):
    return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()}


def test_second_crawl_of_same_site_changes_nothing():
    root = fresh_data()
    crawl(site())
    first = {p: (root / p).read_bytes() for p in files(root)}
    crawl(site())
    assert {p: (root / p).read_bytes() for p in files(root)} == first
    assert "web/telegram.org/tour.html" in first
    assert "web_static/telegram.org/a.css" in first


def test_real_change_is_written():
    root = fresh_data()
    crawl(site())
    crawl(site("v2"))
    assert "v2" in (root / "web/telegram.org/tour.html").read_text(encoding="utf-8")


def test_removed_page_is_deleted():
    root = fresh_data()
    crawl(site())
    pages = site()
    for seed in track.WEB_SEEDS:
        pages[seed] = (200, "text/html", b"<html>no links</html>")
    del pages["https://telegram.org/tour"]
    crawl(pages)
    assert "web/telegram.org/tour.html" not in files(root)


def test_network_failure_keeps_pages():
    root = fresh_data()
    crawl(site())
    before = files(root)
    crawl({seed: (0, "", b"") for seed in track.WEB_SEEDS})
    assert files(root) == before


def test_block_or_stub_for_all_pages_keeps_pages():
    # 403 и заглушка с кодом 200 у всего сайта похожи на удаление: удалять и переписывать нельзя.
    root = fresh_data()
    pages = site()
    for i in range(30):
        url = f"https://core.telegram.org/p{i}"
        pages[url] = (200, "text/html", (PAGE % f"p{i}").encode())
    links = "".join(f'<a href="/p{i}">x</a>' for i in range(30))
    pages["https://core.telegram.org/"] = (200, "text/html", (PAGE % links).encode())
    crawl(pages)
    before = {p: (root / p).read_bytes() for p in files(root)}
    assert len(before) > 30
    crawl({seed: (403, "text/html", b"forbidden") for seed in track.WEB_SEEDS})
    assert {p: (root / p).read_bytes() for p in files(root)} == before, "блокировка"
    stub = {seed: (200, "text/html", b"<html>Too many requests</html>") for seed in track.WEB_SEEDS}
    crawl(stub)
    assert {p: (root / p).read_bytes() for p in files(root)} == before, "заглушка"


def test_stub_for_one_big_page_keeps_it():
    root = fresh_data()
    big = "<p>текст</p>" * 1000
    pages = site()
    pages["https://telegram.org/tour"] = (200, "text/html", big.encode())
    crawl(pages)
    before = (root / "web/telegram.org/tour.html").read_bytes()
    pages["https://telegram.org/tour"] = (200, "text/html", b"<html>Too many requests</html>")
    crawl(pages)
    assert (root / "web/telegram.org/tour.html").read_bytes() == before


def test_limit_mode_never_deletes():
    root = fresh_data()
    crawl(site())
    before = files(root)
    crawl({seed: (200, "text/html", b"<html>x</html>") for seed in track.WEB_SEEDS}, limit=1)
    assert before <= files(root)


def test_unsaveable_address_does_not_stop_crawl():
    root = fresh_data()
    (root / "web/telegram.org/blocked").mkdir(parents=True)
    assert track.write_if_changed(root / "web/telegram.org/blocked", "x") is False
    crawl(site())
    assert "web/telegram.org/tour.html" in files(root)


def translation_page(keys, offset=None):
    rows = "".join(
        f'<div class="tr-key-row" data-key="{k}"><div class="tr-value-default"><span class="value">{v}</span></div>'
        f'<div class="tr-value-key">{k}</div></div>' for k, v in keys.items()
    )
    more = f'<div class="tr-load-more load-more" data-offset="{offset}">' if offset else ""
    return (200, "text/html", (rows + more).encode())


def translation_site(rows):
    pages = {}
    base = track.TRANSLATIONS_URL
    for name in track.TRANSLATION_PLATFORMS:
        pages[f"{base}/en/{name}/"] = (200, "text/html", f'<a href="/en/{name}/general/">g</a>'.encode())
        pages[f"{base}/en/{name}/general/"] = rows
    return pages


def test_translations_follow_pagination():
    root = fresh_data()
    pages = translation_site(translation_page({"A": "a"}, offset=1))
    pages[f"{track.TRANSLATIONS_URL}/en/ios/general/?offset=1"] = translation_page({"B": "b"})
    for name in track.TRANSLATION_PLATFORMS:
        if name != "ios":
            pages[f"{track.TRANSLATIONS_URL}/en/{name}/general/"] = translation_page({"A": "a"})
    asyncio.run(track.track_translations(FakeFetcher(pages), None))
    assert json.loads((root / "translations/ios/general.json").read_text(encoding="utf-8")) == {"A": ["a"], "B": ["b"]}


def test_translation_failures_keep_previous_file():
    root = fresh_data()
    asyncio.run(track.track_translations(FakeFetcher(translation_site(translation_page({"A": "a"}))), None))
    path = root / "translations/ios/general.json"
    before = path.read_bytes()
    asyncio.run(track.track_translations(FakeFetcher({}), None))
    assert path.read_bytes() == before, "сайт недоступен"
    asyncio.run(track.track_translations(FakeFetcher(translation_site((500, "", b""))), None))
    assert path.read_bytes() == before, "раздел с ошибкой"
    asyncio.run(track.track_translations(FakeFetcher(translation_site((200, "text/html", b"<html>new layout</html>"))), None))
    assert path.read_bytes() == before, "пустой разбор, например новая вёрстка, не должен стирать тексты"


def test_tl_failure_keeps_file():
    root = fresh_data()
    good = {url: (200, "text/plain", b"schema") for url in track.TL_SOURCES.values()}
    asyncio.run(track.track_tl(FakeFetcher(good)))
    asyncio.run(track.track_tl(FakeFetcher({})))
    assert (root / "tl/td_api.tl").read_bytes() == b"schema"
    empty = {url: (200, "text/plain", b"") for url in track.TL_SOURCES.values()}
    asyncio.run(track.track_tl(FakeFetcher(empty)))
    assert (root / "tl/td_api.tl").read_bytes() == b"schema", "пустой ответ не должен стирать схему"


class Download:
    def __init__(self, chunks=(b"x",), status=200, length=None, raises=None):
        self.chunks, self.status, self.raises = chunks, status, raises
        self.content_length = length if length is not None else sum(map(len, chunks))
        self.content = self
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise clients.aiohttp.ClientError(f"HTTP {self.status}")

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk
        if self.raises:
            raise self.raises


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **_):
        self.calls += 1
        return self.responses.pop(0)


def fast_sleep():
    async def nothing(_):
        return None
    clients.asyncio.sleep = nothing


def test_download_retries_cut_connection():
    fast_sleep()
    path = Path(tempfile.mkdtemp()) / "b"
    cut = Download((b"ab",), raises=clients.aiohttp.ClientPayloadError("cut"), length=10)
    session = Session(cut, Download((b"abcd",)))
    asyncio.run(clients._download(session, "https://x/y?token=secret", path))
    assert path.read_bytes() == b"abcd" and session.calls == 2


def test_download_gives_up_and_hides_token():
    fast_sleep()
    path = Path(tempfile.mkdtemp()) / "b"
    session = Session(*(Download(status=500) for _ in range(clients.DOWNLOAD_ATTEMPTS)))
    try:
        asyncio.run(clients._download(session, "https://x/y?token=secret", path))
        raise AssertionError("должна быть ошибка")
    except RuntimeError as e:
        assert "secret" not in str(e)


def test_short_download_is_not_accepted():
    fast_sleep()
    path = Path(tempfile.mkdtemp()) / "b"
    session = Session(Download((b"ab",), length=10), Download((b"abcdefghij",), length=10))
    asyncio.run(clients._download(session, "https://x/y", path))
    assert path.read_bytes() == b"abcdefghij"


def test_same_version_is_not_downloaded_and_failed_is_retried():
    root = Path(tempfile.mkdtemp()) / "client"
    (root).mkdir()
    (root / "version.txt").write_text("7\n", encoding="utf-8")
    session = Session()
    asyncio.run(clients._update(session, root, "7", "https://x", lambda *_: None))
    assert session.calls == 0

    def broken(*_):
        raise ValueError("битая сборка")

    session = Session(Download((b"apk",)))
    try:
        asyncio.run(clients._update(session, root, "8", "https://x", broken))
    except ValueError:
        pass
    assert (root / "version.txt").read_text(encoding="utf-8").strip() == "7", "неудача не должна запоминать версию"


def test_one_client_failure_does_not_stop_others():
    class Failing:
        def get(self, url, **_):
            return Download(status=500)

    asyncio.run(clients.track(Failing(), Path(tempfile.mkdtemp())))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    failed = 0
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            try:
                test()
                print("ok", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, repr(e)[:300])
    sys.exit(1 if failed else 0)
