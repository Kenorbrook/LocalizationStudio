"""Validate release layout and file hashes before an update can be staged."""

import hashlib
import json
import stat
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from version import REPOSITORY

ROOT_FILES = {
    "LocalizationStudio.exe",
    "LocalizationWorker.exe",
    "LocalizationUpdater.exe",
    "release-manifest.json",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
}
REQUIRED = {
    "LocalizationStudio.exe",
    "LocalizationWorker.exe",
    "LocalizationUpdater.exe",
    "_internal/ui/index.html",
}


def safe_name(name):
    path = PurePosixPath(name)
    if (
        "\\" in name
        or ":" in name
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.as_posix() != name
        or any(part.endswith((".", " ")) for part in path.parts)
    ):
        raise ValueError("Недопустимый путь в обновлении")
    if path.parts[0] != "_internal" and name not in ROOT_FILES:
        raise ValueError("Обновление пытается изменить пользовательские данные")
    return path


def reject_links(root):
    root = Path(root)
    for path in [root, *root.rglob("*")]:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Каталоги обновления не должны содержать ссылки")


def validate_bundle(bundle, version):
    bundle = Path(bundle).resolve()
    reject_links(bundle)
    manifest = json.loads(
        (bundle / "release-manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("repository") != REPOSITORY or manifest.get("version") != version:
        raise ValueError("Версия или источник сборки не совпадает")
    files = manifest.get("files")
    if (
        not isinstance(files, dict)
        or not REQUIRED <= files.keys()
        or len(files) > 10000
    ):
        raise ValueError("Неполная сборка обновления")
    actual = {
        p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()
    }
    if (
        actual != set(files) | {"release-manifest.json"}
        or "release-manifest.json" in files
    ):
        raise ValueError("В сборке есть файлы вне проверенного списка")
    for name, checksum in files.items():
        safe_name(name)
        path = bundle / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(bundle)
        ):
            raise ValueError("Файл обновления отсутствует или является ссылкой")
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError("Контрольная сумма файла не совпадает: " + name)
    return manifest


def extract_bundle(archive, destination, version):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if (
            len(members) > 10000
            or sum(item.file_size for item in members) > 2 * 1024**3
        ):
            raise ValueError("Сборка превышает допустимый размер")
        names = set()
        for item in members:
            if item.is_dir():
                continue
            name = item.filename.removeprefix("LocalizationStudio/")
            safe_name(name)
            if stat.S_ISLNK(item.external_attr >> 16) or name.casefold() in names:
                raise ValueError("Ссылка или повторяющийся путь в архиве")
            names.add(name.casefold())
            target = destination / name
            if not target.resolve().is_relative_to(destination):
                raise ValueError("Файл находится вне каталога обновления")
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
    validate_bundle(destination, version)
    return destination
