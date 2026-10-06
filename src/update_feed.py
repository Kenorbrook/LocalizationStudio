"""Public GitHub release metadata and bounded, verified downloads."""

import hashlib
import json
import re
import urllib.request
from pathlib import Path
from version import REPOSITORY, VERSION

API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_DOWNLOAD = 500 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("Неподдерживаемый номер версии")
    return tuple(map(int, match.groups()))


def open_remote(url):
    message = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"LocalizationStudio/{VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    return urllib.request.urlopen(message, timeout=20)


def release_asset(release, name):
    asset = next(
        (item for item in release.get("assets", []) if item.get("name") == name), None
    )
    expected = f'https://github.com/{REPOSITORY}/releases/download/{release["tag_name"]}/{name}'
    if not asset or asset.get("browser_download_url") != expected:
        raise ValueError(
            "В релизе нет подходящей сборки или адрес скачивания недопустим"
        )
    return asset


def latest(current=VERSION, opener=open_remote):
    with opener(API) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError("Ответ GitHub слишком большой")
    release = json.loads(content)
    tag = release["tag_name"]
    remote = version_tuple(tag)
    available = (
        remote > version_tuple(current)
        and not release.get("draft")
        and not release.get("prerelease")
    )
    result = {
        "current": current,
        "latest": tag.removeprefix("v"),
        "available": available,
        "url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
        "notes": str(release.get("body") or "")[:20000],
    }
    if available:
        asset = release_asset(release, f"LocalizationStudio-{tag}-windows-x64.zip")
        if not 0 < asset.get("size", 0) <= MAX_DOWNLOAD:
            raise ValueError("Недопустимый размер сборки")
        checksum = release_asset(release, "SHA256SUMS.txt")
        result.update(asset=asset, checksum=checksum)
    return result


def download(
    release, destination, progress=lambda done, total: None, opener=open_remote
):
    asset = release["asset"]
    with opener(release["checksum"]["browser_download_url"]) as response:
        checksums = response.read(64001)
    if len(checksums) > 64000:
        raise ValueError("Файл контрольных сумм слишком большой")
    expected = None
    for line in checksums.decode("utf-8-sig").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match[2] == asset["name"]:
            expected = match[1].lower()
    if not expected:
        raise ValueError("Для сборки нет контрольной суммы SHA-256")
    destination = Path(destination)
    temporary = destination.with_suffix(".partial")
    digest = hashlib.sha256()
    received = 0
    try:
        with (
            opener(asset["browser_download_url"]) as response,
            temporary.open("wb") as stream,
        ):
            while chunk := response.read(256 * 1024):
                received += len(chunk)
                if received > asset["size"] or received > MAX_DOWNLOAD:
                    raise ValueError("Размер скачанной сборки превышает заявленный")
                stream.write(chunk)
                digest.update(chunk)
                progress(received, asset["size"])
        if received != asset["size"] or digest.hexdigest() != expected:
            raise ValueError("Сборка повреждена: размер или SHA-256 не совпадает")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
