"""Validated, bounded plugin ZIPs and atomic activation metadata."""

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from version import VERSION

MAX_SIZE = 20_000_000


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Некорректная версия плагина")
    return tuple(map(int, value.split(".")))


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise ValueError("Некорректное имя плагина")
    return value


def safe_path(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Некорректный путь в пакете")
    parts = PurePosixPath(value).parts
    if (
        not parts
        or value.startswith("/")
        or any(
            p in {".", ".."}
            or p.endswith((".", " "))
            or re.search(r'[<>"|?*\x00-\x1f]', p)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", p)
            for p in parts
        )
        or str(PurePosixPath(value)) != value
    ):
        raise ValueError("Небезопасный путь в пакете")
    return value


def guard(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in [path, *path.parents]):
        raise ValueError("Пакеты плагинов не должны быть ссылками")
    return path


def manifest(value):
    identity(value.get("id"))
    version(value.get("version"))
    if value.get("api_version") != 1 or version(value.get("min_app_version")) > version(
        VERSION
    ):
        raise ValueError("Плагин требует другую версию Studio")
    if not isinstance(value.get("permissions"), list) or not set(
        value["permissions"]
    ) <= {"ui", "theme", "backend", "project.read", "local.model"}:
        raise ValueError("Неизвестные разрешения плагина")
    for field in ["name", "description", "category"]:
        if not isinstance(value.get(field), str) or not 1 <= len(value[field]) <= 600:
            raise ValueError("Некорректное описание плагина")
    for field in ["ui", "backend"]:
        if value.get(field):
            safe_path(value[field])
    if value.get("backend") and (
        value["backend"] != "__init__.py" or "backend" not in value["permissions"]
    ):
        raise ValueError("Некорректная точка входа плагина")
    if value.get("ui") and (
        not value["ui"].endswith(".js") or "ui" not in value["permissions"]
    ):
        raise ValueError("Некорректная точка входа интерфейса")
    return value


def read_package(path):
    if Path(path).stat().st_size > MAX_SIZE:
        raise ValueError("Пакет больше 20 МБ")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= 500 or sum(i.file_size for i in infos) > MAX_SIZE:
            raise ValueError("Превышены ограничения пакета")
        names = set()
        files = {}
        for info in infos:
            name = safe_path(info.filename)
            if (
                name.casefold() in names
                or info.is_dir()
                or stat.S_ISLNK(info.external_attr >> 16)
            ):
                raise ValueError("Повторяющийся путь или ссылка в пакете")
            names.add(name.casefold())
            files[name] = archive.read(info)
        value = manifest(json.loads(files["plugin.json"]))
        hashes = value.get("files")
        if not isinstance(hashes, dict) or set(hashes) != set(files) - {"plugin.json"}:
            raise ValueError("Состав пакета не совпадает с манифестом")
        for name, digest in hashes.items():
            if hashlib.sha256(files[name]).hexdigest() != digest:
                raise ValueError("Контрольная сумма файла не совпадает")
        for field in ["ui", "backend"]:
            if value.get(field) and value[field] not in files:
                raise ValueError("Нет точки входа плагина")
        return value, files


def atomic_json(path, value):
    guard(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    guard(temporary)
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
