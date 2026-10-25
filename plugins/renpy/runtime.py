"""Use the game's own Ren'Py runtime, preserving its engine version."""

import os
import re
import base64
import json
import subprocess
from pathlib import Path

from .filesystem import guard


def require_not_running(root):
    if os.name != "nt":
        return
    prefix = str(Path(root).resolve()).rstrip("\\/") + "\\"
    literal = prefix.replace("'", "''")
    script = (
        "$p='"
        + literal
        + "'; @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($p,[StringComparison]::OrdinalIgnoreCase) } | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise ValueError("Не удалось проверить, закрыта ли копия игры")
    if result.stdout.strip() and json.loads(result.stdout):
        raise ValueError(
            "Закройте игру перед изменением её копии. Studio не завершает игру автоматически"
        )


def discover(root):
    root = guard(root).resolve()
    if root.name.lower() == "game" and (root.parent / "renpy").is_dir():
        root = root.parent
    if not (root / "game").is_dir() or not (root / "renpy/__init__.py").is_file():
        raise ValueError("Нужна папка Windows-игры Ren’Py с game, renpy и lib")
    text = guard(root / "renpy/__init__.py").read_text(encoding="utf-8-sig")
    match = re.search(r"version_tuple\s*=\s*\(\s*([78])\s*,\s*(\d+)\s*,\s*(\d+)", text)
    if not match:
        version_file = guard(root / "renpy/vc_version.py")
        version_text = (
            version_file.read_text(encoding="utf-8-sig")
            if version_file.is_file()
            else text
        )
        match = re.search(r"(?m)^version\s*=\s*['\"]([78])\.(\d+)\.(\d+)", version_text)
    if not match:
        raise ValueError("Версия Ren’Py не распознана. Поддерживаются семейства 7 и 8")
    python = next(
        (
            guard(root / "lib" / folder / "python.exe")
            for folder in ["py3-windows-x86_64", "windows-x86_64", "windows-i686"]
            if (root / "lib" / folder / "python.exe").is_file()
        ),
        None,
    )
    if not python:
        raise ValueError(
            "Не найден встроенный Windows Python игры. Смена версии движка автоматически не выполняется"
        )
    launchers = []
    for path in sorted(root.glob("*.py")):
        guard(path)
        if path.stat().st_size < 100000 and "renpy.bootstrap" in path.read_text(
            encoding="utf-8-sig"
        ):
            launchers.append(path)
    if len(launchers) != 1:
        raise ValueError(
            "Не найден единственный штатный .py launcher Ren’Py рядом с game"
        )
    return dict(
        root=str(root),
        python=python.relative_to(root).as_posix(),
        launcher=launchers[0].name,
        version=".".join(match.groups()),
    )


def command(root, task, savedir, *args):
    found = discover(root)
    return [
        str(Path(root) / found["python"]),
        *(["-X", "utf8"] if found["version"].startswith("8.") else []),
        str(Path(root) / found["launcher"]),
        "--savedir",
        str(savedir),
        str(root),
        task,
        *args,
    ]


def environment(home, *, headless=True, language=None, root=None):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "_PYI_", "RENPY_"))
        and key.upper()
        not in {
            "VIRTUAL_ENV",
            "RENPY_LANGUAGE",
            "RENPY_UPDATE_STRINGS",
            "STUDIO_RENPY_LANGUAGE",
        }
    }
    env.update(PYTHONIOENCODING="utf-8", RENPY_SKIP_RECOMPILE="0")
    if root is not None:
        found = discover(root)
        env["PYTHONHOME"] = str(root)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(root) / Path(found["python"]).parent), str(root)]
        )
    if headless:
        env.update(
            SDL_VIDEODRIVER="dummy",
            SDL_AUDIODRIVER="dummy",
            RENPY_HEADLESS="1",
            RENPY_SIMPLE_EXCEPTIONS="1",
        )
        for name in ["APPDATA", "LOCALAPPDATA"]:
            folder = Path(home) / name.lower()
            folder.mkdir(parents=True, exist_ok=True)
            env[name] = str(folder)
    if language is not None:
        env["STUDIO_RENPY_LANGUAGE"] = language
    return env
