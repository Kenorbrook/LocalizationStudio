"""Reconstruct a verified bundle from installed files and individual release assets."""

import gzip
import hashlib
import json
import re
import shutil
from pathlib import Path

from update_feed import MAX_DOWNLOAD, checksum_for, open_remote, release_asset
from update_package import REQUIRED, reject_links, safe_name, validate_bundle
from version import REPOSITORY

MAX_MANIFEST = 2_000_000
MAX_UNPACKED = 2 * 1024**3


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_index(index, version):
    if (
        not isinstance(index, dict)
        or index.get("schema") != 1
        or index.get("repository") != REPOSITORY
        or index.get("version") != version
    ):
        raise ValueError("Версия или источник списка обновления не совпадает")
    files = index.get("files")
    if (
        not isinstance(files, dict)
        or not REQUIRED | {"release-manifest.json"} <= files.keys()
        or len(files) > 10000
    ):
        raise ValueError("Неполный список файлов обновления")
    seen = set()
    total = compressed = 0
    for name, entry in files.items():
        safe_name(name)
        key = name.casefold()
        if key in seen or not isinstance(entry, dict):
            raise ValueError("Повторяющийся путь в обновлении")
        seen.add(key)
        for field in ["sha256", "download_sha256"]:
            if not isinstance(entry.get(field), str) or not re.fullmatch(
                r"[0-9a-f]{64}", entry[field]
            ):
                raise ValueError("Некорректная контрольная сумма обновления")
        if entry.get("asset") != f'file-{entry["sha256"]}.gz':
            raise ValueError("Недопустимое имя файла загрузки")
        for field in ["size", "download_size"]:
            if (
                type(entry.get(field)) is not int
                or not 0 <= entry[field] <= MAX_DOWNLOAD
            ):
                raise ValueError("Недопустимый размер файла обновления")
        if not entry["download_size"]:
            raise ValueError("Пустой файл загрузки")
        total += entry["size"]
        compressed += entry["download_size"]
    if total > MAX_UNPACKED or compressed > MAX_DOWNLOAD:
        raise ValueError("Обновление превышает допустимый размер")
    for key in seen:
        if any(
            parent.as_posix() in seen
            for parent in Path(key).parents
            if parent.as_posix() != "."
        ):
            raise ValueError("Файл совпадает с каталогом обновления")
    return files


def read_index(release, opener):
    asset = release["incremental"]["manifest"]
    if type(asset.get("size")) is not int or not 0 < asset["size"] <= MAX_MANIFEST:
        raise ValueError("Недопустимый размер списка обновления")
    expected = checksum_for(release, asset["name"], opener)
    with opener(asset["browser_download_url"]) as response:
        raw = response.read(MAX_MANIFEST + 1)
    if len(raw) != asset["size"] or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Список обновления повреждён: SHA-256 или размер не совпадает")
    index = json.loads(raw)
    validate_index(index, release["latest"])
    return index


def copy_installed(home, name, entry, target):
    source = home / name
    for part in [source, *source.parents]:
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("Файлы приложения не должны содержать ссылки")
        if part == home:
            break
    try:
        if not source.is_file() or source.stat().st_size != entry["size"]:
            return False
        # Validate the copy, so a concurrent change cannot enter the staged bundle.
        shutil.copyfile(source, target)
        if (
            target.stat().st_size == entry["size"]
            and file_hash(target) == entry["sha256"]
        ):
            return True
    except OSError:
        pass
    target.unlink(missing_ok=True)
    return False


def fetch_file(asset, entry, packed, target, progress, opener):
    digest = hashlib.sha256()
    received = 0
    try:
        with (
            opener(asset["browser_download_url"]) as response,
            packed.open("wb") as stream,
        ):
            while chunk := response.read(256 * 1024):
                received += len(chunk)
                if received > entry["download_size"]:
                    raise ValueError("Размер скачанного файла превышает заявленный")
                digest.update(chunk)
                stream.write(chunk)
                progress(len(chunk))
        if (
            received != entry["download_size"]
            or digest.hexdigest() != entry["download_sha256"]
        ):
            raise ValueError(
                "Файл обновления повреждён: размер или SHA-256 не совпадает"
            )
        written = 0
        with gzip.open(packed, "rb") as stream, target.open("wb") as output:
            while chunk := stream.read(256 * 1024):
                written += len(chunk)
                if written > entry["size"]:
                    raise ValueError("Распакованный файл превышает заявленный размер")
                output.write(chunk)
        if written != entry["size"] or file_hash(target) != entry["sha256"]:
            raise ValueError("Распакованный файл повреждён: SHA-256 не совпадает")
    finally:
        packed.unlink(missing_ok=True)


def stage_files(
    release, home, destination, progress=lambda **values: None, opener=open_remote
):
    """Copy only verified local matches; fetch everything else without a full-ZIP fallback."""
    index = read_index(release, opener)
    home = Path(home).absolute()
    destination = Path(destination).absolute()
    reject_links(destination.parent)
    destination.mkdir(exist_ok=False)
    files = index["files"]
    pending = {}
    reused = reused_bytes = 0
    progress(
        message="Проверяем установленные файлы…", downloaded=0, total=0, reused_files=0
    )
    for name, entry in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if copy_installed(home, name, entry, target):
            reused += 1
            reused_bytes += entry["size"]
        else:
            pending.setdefault(entry["asset"], []).append((name, entry))
    remote = release["incremental"]["release"]
    downloads = []
    for asset_name, entries in pending.items():
        asset = release_asset(remote, asset_name)
        if asset.get("size") != entries[0][1]["download_size"]:
            raise ValueError("Размер файла в релизе не совпадает со списком обновления")
        if any(entry != entries[0][1] for _, entry in entries):
            raise ValueError("Противоречивые сведения о файле обновления")
        downloads.append((asset, entries))
    total = sum(asset["size"] for asset, _ in downloads)
    received = 0

    def advance(count):
        nonlocal received
        received += count
        progress(downloaded=received, total=total)

    progress(
        message=(
            "Скачиваем изменённые файлы…"
            if total
            else "Все файлы уже есть; проверяем сборку…"
        ),
        total=total,
        reused_files=reused,
        reused_bytes=reused_bytes,
        changed_files=sum(len(entries) for _, entries in downloads),
        mode="files",
    )
    for asset, entries in downloads:
        name, entry = entries[0]
        target = destination / name
        fetch_file(
            asset, entry, destination.parent / "file.partial", target, advance, opener
        )
        for other, _ in entries[1:]:
            shutil.copyfile(target, destination / other)
    validate_bundle(destination, release["latest"])
    manifest = json.loads(
        (destination / "release-manifest.json").read_text(encoding="utf-8")
    )
    if set(manifest["files"]) != files.keys() - {"release-manifest.json"} or any(
        checksum != files[name]["sha256"]
        for name, checksum in manifest["files"].items()
    ):
        raise ValueError("Списки файлов сборки не совпадают")
    return destination
