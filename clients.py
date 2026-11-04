"""Снимки сборок Telegram: что в них нового можно понять без запуска.

Android: имена TL-классов, ключи и тексты из dex, манифест, ресурсы, список файлов.
macOS: строки интерфейса, Info.plist и список файлов из zip обновления.
Сборка скачивается, только если её версия изменилась.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import plistlib
import re
import struct
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path

import aiohttp

logger = logging.getLogger("track.clients")

ANDROID_BETA_JSON = "https://telegram.org/dl/android/apk-public-beta.json"
ANDROID_STABLE_APK = "https://telegram.org/dl/android/apk"
MACOS_FEED = "https://osx.telegram.org/updates/versions.xml"
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=1500)

TL_CLASS = re.compile(r"Lorg/telegram/tgnet/(?:TLRPC\$|tl/TL_\w+\$|tl/)(\w+);")
NAMED_CLASS = re.compile(r"L(org/telegram/[\w/$]+);")
SNAKE_KEY = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
HAS_WORD = re.compile(r"[^\W\d_]{2}")
OBFUSCATED_SEGMENT = re.compile(r"[a-z][a-z0-9]{0,3}|[A-Z]\d*")
SQL_TEXT = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WHERE|ORDER BY|VALUES)\b")
SHADER_TEXT = re.compile(r"[{};]\s*$|gl_FragColor|texture2D|uniform ", re.M)
MAX_TEXT_LEN = 400
# Меняются в каждой сборке и ни о чём не говорят.
FILE_NOISE = re.compile(r"META-INF/|_CodeSignature/|assets/dexopt/")


def _uleb128(data: bytes, offset: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return result, offset


def dex_strings(data: bytes):
    count, table = struct.unpack_from("<II", data, 0x38)
    for index in range(count):
        offset = struct.unpack_from("<I", data, table + index * 4)[0]
        _, start = _uleb128(data, offset)
        yield data[start:data.index(0, start)].decode("utf-8", "replace")


def classify_strings(strings) -> dict[str, set[str]]:
    """Обфусцированные имена (messenger/c30) отбрасываются: они другие в каждой сборке."""
    out: dict[str, set[str]] = {"tl": set(), "classes": set(), "keys": set(), "text": set()}
    for value in strings:
        if value.startswith("L") and value.endswith(";"):
            match = TL_CLASS.fullmatch(value)
            if match and "TL_" in value:
                owner = re.search(r"tl/(TL_\w+)\$", value)
                out["tl"].add(f"{owner.group(1)}.{match.group(1)}" if owner else match.group(1))
                continue
            named = NAMED_CLASS.fullmatch(value)
            if named:
                last = re.split(r"[/$]", named.group(1))[-1]
                if not OBFUSCATED_SEGMENT.fullmatch(last) and not last.isdigit():
                    out["classes"].add(named.group(1).replace("org/telegram/", ""))
        elif value.startswith(("[", "(")):
            continue
        elif SNAKE_KEY.fullmatch(value):
            out["keys"].add(value)
        elif len(value) <= MAX_TEXT_LEN and HAS_WORD.search(value) and (
            " " in value or value.startswith(("http://", "https://"))
        ) and not SHADER_TEXT.search(value) and not SQL_TEXT.search(value):
            out["text"].add(" ".join(value.split()))
    return out


def file_list(archive: zipfile.ZipFile) -> str:
    rows = [f"{info.filename}\t{info.file_size}\t{info.CRC:08x}" for info in archive.infolist()
            if not info.is_dir() and not FILE_NOISE.search(info.filename)]
    return "\n".join(sorted(rows)) + "\n"


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def snapshot_apk(path: str, root: Path) -> None:
    from loguru import logger as androguard_logger
    androguard_logger.remove()
    from androguard.core.apk import APK

    apk = APK(path)
    strings: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if re.fullmatch(r"classes\d*\.dex", name):
                strings.update(dex_strings(archive.read(name)))
        _write(root, "files.tsv", file_list(archive))
    for group, values in classify_strings(strings).items():
        _write(root, f"{group}.txt", "\n".join(sorted(values)) + "\n")
    manifest = {
        "version": apk.get_androidversion_name(), "code": apk.get_androidversion_code(),
        "package": apk.get_package(), "min_sdk": apk.get_min_sdk_version(),
        "target_sdk": apk.get_target_sdk_version(), "permissions": sorted(apk.get_permissions()),
        "activities": sorted(apk.get_activities()), "services": sorted(apk.get_services()),
        "receivers": sorted(apk.get_receivers()), "providers": sorted(apk.get_providers()),
        "features": sorted(apk.get_features()),
    }
    _write(root, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    resources = apk.get_android_resources()
    package = resources.get_packages_names()[0]
    values: dict[str, str] = {}
    for locale in resources.get_locales(package):
        if locale in ("DEFAULT", "\x00\x00"):
            xml = resources.get_string_resources(package, locale)
            for node in ElementTree.fromstring(xml).iter("string"):
                values[node.get("name")] = node.text or ""
    values.pop("com.google.firebase.crashlytics.mapping_file_id", None)
    _write(root, "res_strings.json", json.dumps(values, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _read_strings(data: bytes) -> dict[str, str] | None:
    """.strings бывает бинарным plist, XML-plist или текстом вида "ключ" = "значение";"""
    try:
        parsed = plistlib.loads(data)
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else None
    except Exception:
        pass
    for encoding in ("utf-16", "utf-8"):
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        rows = re.findall(r'^\s*"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', text, re.M)
        if rows:
            return dict(rows)
    return None


def snapshot_macos(path: str, root: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        _write(root, "files.tsv", file_list(archive))
        # Вложенные приложения и компоненты тоже имеют Info.plist, главный лежит ближе всех к корню.
        plists = [n for n in archive.namelist() if n.endswith(".app/Contents/Info.plist")]
        info = min(plists, key=lambda n: n.count("/"), default=None)
        if info:
            plist = plistlib.loads(archive.read(info))
            keep = {k: plist.get(k) for k in ("CFBundleShortVersionString", "CFBundleVersion", "LSMinimumSystemVersion")}
            _write(root, "info.json", json.dumps(keep, ensure_ascii=False, indent=1) + "\n")
        for name in archive.namelist():
            if re.search(r"/en\.lproj/[^/]+\.strings$", name):
                values = _read_strings(archive.read(name))
                if values:
                    rel = Path(name).name.replace(".strings", ".json")
                    _write(root, f"strings/{rel}", json.dumps(values, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


async def _download(session: aiohttp.ClientSession, url: str, path: Path) -> None:
    async with session.get(url, timeout=DOWNLOAD_TIMEOUT) as resp:
        resp.raise_for_status()
        expected = resp.content_length
        with path.open("wb") as handle:
            async for chunk in resp.content.iter_chunked(1 << 20):
                handle.write(chunk)
    if expected is not None and path.stat().st_size != expected:
        raise RuntimeError(f"{url}: скачано {path.stat().st_size} из {expected}")


async def _update(session: aiohttp.ClientSession, root: Path, version: str, url: str, snapshot) -> None:
    marker = root / "version.txt"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == version:
        logger.info("%s: без изменений (%s)", root.name, version)
        return
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "build"
        await _download(session, url, path)
        await asyncio.to_thread(snapshot, str(path), root)
    _write(root, "version.txt", version + "\n")
    logger.info("%s: снята версия %s", root.name, version)


async def track(session: aiohttp.ClientSession, root: Path) -> None:
    async def android_beta() -> None:
        async with session.get(ANDROID_BETA_JSON) as resp:
            resp.raise_for_status()
            info = await resp.json(content_type=None)
        _write(root / "android-beta", "changelog.txt", str(info.get("changelog") or "") + "\n")
        await _update(session, root / "android-beta", str(info["version_code"]), info["file_url"], snapshot_apk)

    async def android_stable() -> None:
        async with session.get(ANDROID_STABLE_APK, headers={"Range": "bytes=0-0"}) as resp:
            resp.raise_for_status()
            # Версии стабильная ссылка не сообщает, поэтому смену сборки видно по размеру файла.
            size = resp.headers.get("Content-Range", "").rsplit("/", 1)[-1]
        if not size.isdigit():
            raise RuntimeError("android-stable: сервер не сообщил размер файла")
        await _update(session, root / "android-stable", size, ANDROID_STABLE_APK, snapshot_apk)

    async def macos() -> None:
        async with session.get(MACOS_FEED) as resp:
            resp.raise_for_status()
            feed = await resp.text()
        items = re.findall(r'url="([^"]+\.zip)"[^>]*?sparkle:version="(\d+)"|sparkle:version="(\d+)"[^>]*?url="([^"]+\.zip)"', feed)
        builds = [(int(a or b), u or v) for u, a, b, v in items]
        if not builds:
            raise RuntimeError("macos: в ленте нет сборок")
        version, url = max(builds)
        await _update(session, root / "macos", str(version), url, snapshot_macos)

    results = await asyncio.gather(android_beta(), android_stable(), macos(), return_exceptions=True)
    for name, result in zip(("android-beta", "android-stable", "macos"), results):
        if isinstance(result, Exception):
            logger.warning("%s: %s", name, result)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
