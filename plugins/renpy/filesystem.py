"""Copy ownership and traversal guards. No writes to the source game."""

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

MARKER = ".studio-renpy-copy.json"


def guard(path):
    path = Path(path).absolute()
    for item in [path, *path.parents]:
        if item.is_symlink() or item.is_junction():
            raise ValueError("Ссылки и junctions в путях игры не поддерживаются")
        if item.exists() and getattr(item.stat(), "st_file_attributes", 0) & 1024:
            raise ValueError("Reparse point в пути игры не поддерживается")
    return path


def tree(root):
    root = guard(root)
    pending = [root]
    while pending:
        for entry in os.scandir(pending.pop()):
            path = guard(Path(entry.path))
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                yield path
            else:
                raise ValueError("Неподдерживаемый тип файла")


def output_path(source, destination):
    source, destination = guard(source).resolve(), guard(destination).resolve()
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        raise ValueError("Копия должна находиться отдельно от исходной игры")
    if destination.exists():
        raise ValueError("Папка результата уже существует. Выберите новую папку")
    if not destination.parent.is_dir():
        raise ValueError("Родительская папка результата не существует")
    return source, destination


def write_json(path, value):
    path = guard(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = guard(path.with_suffix(".tmp"))
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def owned(root, token):
    root = guard(root).resolve()
    marker = json.loads(guard(root / MARKER).read_text(encoding="utf-8"))
    if marker.get("owner") != token or marker.get("copy") != str(root):
        raise ValueError("Не подтверждено владение подготовленной копией")
    return root


def copy_game(source, destination, token, progress, cancelled):
    source, destination = output_path(source, destination)
    paths = list(tree(source))
    total = sum(p.stat().st_size for p in paths)
    if shutil.disk_usage(destination.parent).free < total + 100_000_000:
        raise ValueError("Недостаточно свободного места для отдельной копии игры")
    if any(p.relative_to(source).as_posix() == MARKER for p in paths):
        raise ValueError("Эта папка уже является копией Studio")
    destination.mkdir()
    write_json(
        destination / MARKER,
        {"owner": token, "source": str(source), "copy": str(destination)},
    )
    copied = 0
    manifest = {}
    for path in paths:
        if cancelled.is_set():
            raise ValueError("Подготовка остановлена; исходная игра не изменена")
        relative = path.relative_to(source)
        target = guard(destination / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as input_file, target.open("xb") as output:
            while block := input_file.read(4 * 1024 * 1024):
                if cancelled.is_set():
                    raise ValueError("Копирование остановлено")
                output.write(block)
                digest.update(block)
                copied += len(block)
                progress(copied, total, relative.as_posix())
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Файл игры изменился при копировании")
        shutil.copystat(path, target)
        manifest[relative.as_posix()] = {
            "sha256": digest.hexdigest(),
            "size": before.st_size,
        }
    return manifest
