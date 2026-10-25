"""Plugin feeds use their own tags; never the application's latest release."""

import hashlib
import json
import urllib.request

from plugin_package import MAX_SIZE, guard, identity, version
from version import REPOSITORY


def fetch(url, limit=MAX_SIZE):
    if not url.startswith(
        (
            f"https://api.github.com/repos/{REPOSITORY}/",
            f"https://github.com/{REPOSITORY}/releases/download/",
        )
    ):
        raise ValueError("Недоверенный источник обновления")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "LocalizationStudio-Plugins",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Обновление превышает допустимый размер")
    return data


def latest(key, installed):
    identity(key)
    prefix = f"plugin-{key}-v"
    refs = json.loads(
        fetch(
            f"https://api.github.com/repos/{REPOSITORY}/git/matching-refs/tags/{prefix}",
            1_000_000,
        )
    )
    candidates = []
    for item in refs:
        tag = item["ref"].removeprefix("refs/tags/")
        if tag.startswith(prefix):
            try:
                if version(tag[len(prefix) :]) > version(installed):
                    candidates.append(tag)
            except ValueError:
                pass
    for tag in sorted(
        candidates, key=lambda t: version(t[len(prefix) :]), reverse=True
    ):
        release = json.loads(
            fetch(
                f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}",
                1_000_000,
            )
        )
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = {a["name"]: a for a in release["assets"]}
        filename = tag + ".zip"
        if filename not in assets or "SHA256SUMS.txt" not in assets:
            continue
        if assets[filename]["size"] > MAX_SIZE:
            raise ValueError("Пакет плагина слишком большой")
        return dict(
            phase="available",
            message="Доступна версия " + tag[len(prefix) :],
            version=tag[len(prefix) :],
            filename=filename,
            url=assets[filename]["browser_download_url"],
            checksum_url=assets["SHA256SUMS.txt"]["browser_download_url"],
        )
    return {"phase": "current", "message": "Установлена последняя версия пакета"}


def download(offer, target):
    checksums = fetch(offer["checksum_url"], 10000).decode("ascii")
    wanted = None
    for line in checksums.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == offer["filename"]:
            wanted = fields[0]
    payload = fetch(offer["url"])
    if not wanted or hashlib.sha256(payload).hexdigest() != wanted:
        raise ValueError("Контрольная сумма пакета не совпадает")
    guard(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
