"""Owned project files. The translation database retains archived projects."""

import ctypes
import os
import shutil
import stat
from pathlib import Path
from uuid import uuid4

FOLDER = ".localization-studio"
MARKER = "owner"


def linked(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def inside(folder, name):
    if name != Path(name).name or name in {"", ".", ".."}:
        raise ValueError("Некорректное имя служебного файла")
    path = folder / name
    if linked(path) or path.resolve().parent != folder:
        raise ValueError("Служебный путь ведёт за пределы папки проекта")
    return path


def own_folder(project):
    root = Path(project["root"]).resolve()
    folder = inside(root, FOLDER)
    if not folder.is_dir():
        return None
    marker = inside(folder, MARKER)
    token = project["workspace_token"]
    if not token or not marker.is_file() or marker.stat().st_size != len(token):
        return None
    return folder if marker.read_bytes() == token.encode("ascii") else None


def lookup(store, project_id):
    with store.db() as db:
        project = db.execute(
            "SELECT root,workspace_token FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    return own_folder(project) if project else None


def set_hidden(folder):
    if os.name == "nt":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        api.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        flags = api.GetFileAttributesW(str(folder))
        if flags == -1 or not api.SetFileAttributesW(
            str(folder), flags | stat.FILE_ATTRIBUTE_HIDDEN
        ):
            raise ctypes.WinError(ctypes.get_last_error())


def ensure(store, project_id):
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        project = db.execute(
            "SELECT root,workspace_token FROM projects WHERE id=? AND deleting=0",
            (project_id,),
        ).fetchone()
        if not project:
            raise ValueError("Проект удалён или удаляется")
        old = own_folder(project)
        if old is not None:
            return old
        root = Path(project["root"]).resolve(strict=True)
        folder = inside(root, FOLDER)
        if folder.exists():
            raise ValueError(
                "Служебная папка уже существует и не принадлежит этому проекту"
            )
        token = project["workspace_token"] or uuid4().hex
        folder.mkdir()
        marker = folder / MARKER
        try:
            marker.write_text(token, encoding="ascii")
            set_hidden(folder)
            db.execute(
                "UPDATE projects SET workspace_token=? WHERE id=?", (token, project_id)
            )
        except BaseException:
            marker.unlink(missing_ok=True)
            folder.rmdir()
            raise
        return folder


def remove_owned(store, project_id):
    with store.db() as db:
        project = db.execute(
            "SELECT root,workspace_token FROM projects WHERE id=? AND deleting=1",
            (project_id,),
        ).fetchone()
    if not project or not project["workspace_token"]:
        return
    folder = own_folder(project)
    if folder is None:
        return
    if folder.resolve() != Path(project["root"]).resolve() / FOLDER:
        raise ValueError("Служебная папка находится вне проекта")
    pending = [folder]
    while pending:
        parent = pending.pop()
        if linked(parent):
            raise ValueError("В служебной папке обнаружена ссылка")
        for path in parent.iterdir():
            if linked(path):
                raise ValueError("В служебной папке обнаружена ссылка")
            if path.is_dir():
                pending.append(path)
    shutil.rmtree(folder)
