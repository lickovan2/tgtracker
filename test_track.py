"""Запуск: python test_track.py"""
from pathlib import Path

import clients
import track


def test_normalize_removes_volatile_parts_and_splits_tags():
    a = '<p>x</p><!-- page generated in 5.21ms --><a href="/api?hash=abc123">y</a>'
    b = '<p>x</p><!-- page generated in 9.00ms --><a href="/api?hash=def456">y</a>'
    assert track.normalize_html(a) == track.normalize_html(b)
    assert "\n<a" in track.normalize_html(a)


def test_links_keep_only_crawlable_pages_without_query():
    page = (
        '<a href="/method/x?layer=5#top">m</a><a href="https://evil.example/p">e</a>'
        '<a href="/dl/android/apk">d</a><a href="https://telegram.org/blog?setln=ru">b</a>'
        '<link href="/css/a.css?3"><script src="/js/a.js"></script><img src="/img/a.png">'
    )
    pages, assets = track.links_of(page, "https://core.telegram.org/api")
    assert pages == {"https://core.telegram.org/method/x", "https://telegram.org/blog"}
    assert assets == {
        "https://core.telegram.org/css/a.css?3", "https://core.telegram.org/js/a.js",
        "https://core.telegram.org/img/a.png",
    }


def test_links_skip_user_generated_pages():
    page = (
        '<a href="https://instantview.telegram.org/contest/x.com/">c</a><a href="https://instantview.telegram.org/docs">d</a>'
        '<a href="https://contest.com/user/Name">u</a><a href="https://contest.com/gif/entry12">e</a>'
        '<a href="https://contest.com/gif">g</a><a href="https://core.telegram.org/tdlib/docs/class">t</a>'
        '<a href="https://core.telegram.org/tdlib">l</a>'
    )
    pages, _assets = track.links_of(page, "https://telegram.org/")
    assert pages == {
        "https://instantview.telegram.org/docs", "https://contest.com/gif", "https://core.telegram.org/tdlib",
    }


def test_relative_link_trap_is_not_followed():
    assert track.is_link_trap("/evolution/t.me/BLOGLINK")
    assert track.is_link_trap("/a/b/a")
    assert track.is_link_trap("/1/2/3/4/5/6/7")
    assert not track.is_link_trap("/api/bots/commands")
    pages, _assets = track.links_of('<a href="t.me/x">x</a><a href="/tour/">t</a>', "https://telegram.org/evolution/")
    assert pages == {"https://telegram.org/tour/"}


def test_page_path_stays_inside_root_and_is_safe():
    root = Path("r")
    assert track.page_path(root, "https://telegram.org/") == root / "telegram.org" / "index.html"
    assert track.page_path(root, "https://x.org/a/../b%3Fq") == root / "x.org" / "a" / "b_q.html"
    assert track.page_path(root, "https://x.org/a.css?3", is_html=False) == root / "x.org" / "a.css"


def test_translation_rows_take_default_text_not_suggestions():
    page = (
        '<div class="tr-key-row" data-key="Hello"><div class="tr-value-default">'
        '<span class="value">Hi &amp; <b>bye</b></span></div><div class="tr-value-key">Hello</div>'
        '<div class="tr-value-suggestions"><span class="value">чужой вариант</span></div></div>'
        '<div class="tr-key-row" data-key="Count"><div class="tr-value-default">'
        '<span class="value">one</span><span class="value">many</span></div><div class="tr-value-key">Count</div></div>'
    )
    assert track.parse_translation_rows(page) == {"Hello": ["Hi & bye"], "Count": ["one", "many"]}


def test_classify_drops_obfuscated_names_and_shaders():
    groups = clients.classify_strings([
        "Lorg/telegram/tgnet/TLRPC$TL_userWallet;", "Lorg/telegram/tgnet/tl/TL_update$TL_updateX;",
        "Lorg/telegram/messenger/c30;", "Lorg/telegram/ui/ChatActivity;", "[Lorg/telegram/messenger/ad;",
        "forum_sidebar", "Video failed", "SELECT a FROM b", "gl_FragColor = vec4(1.0);\n}",
    ])
    assert groups == {
        "tl": {"TL_userWallet", "TL_update.TL_updateX"}, "classes": {"ui/ChatActivity"},
        "keys": {"forum_sidebar"}, "text": {"Video failed"},
    }


def test_read_strings_handles_text_format():
    data = '"a" = "b";\n"q\\"x" = "y";\n'.encode("utf-16")
    assert clients._read_strings(data) == {"a": "b", 'q\\"x': "y"}


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
