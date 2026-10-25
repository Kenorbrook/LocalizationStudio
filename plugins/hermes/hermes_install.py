"""Optional, pinned upstream Hermes installer, isolated from the application bundle."""

import hashlib
import os
import subprocess
import urllib.request
from pathlib import Path

COMMIT = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"
VERSION = "0.22.5"
INSTALLER_HASH = "8d491a8b9b19934bb8d027b11962c394551798e969d244b68d4ab95295b970e2"
URL = f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{COMMIT}/scripts/install.ps1"
RESOURCES = Path(__file__).resolve().parent / "resources"
STAGES = [
    ("prerequisites", "Проверяем инструменты установки…"),
    ("repository", "Скачиваем Hermes Agent…"),
    ("venv", "Подготавливаем отдельный Python…"),
    ("python-deps", "Устанавливаем зависимости Hermes…"),
]


def guard_root(root):
    root = Path(root).absolute()
    # Upstream PM uses links inside its tool store. Our writable entry points
    # and their parents must stay ordinary paths; never traverse the tool store.
    paths = [
        root,
        *root.parents,
        root / "home",
        root / "home/hermes-agent",
        root / "runs",
        root / "install.ps1",
        root / "preferences.json",
        root / "installed.json",
        root / "home/config.yaml",
    ]
    if any(
        path.is_symlink() or getattr(path, "is_junction", lambda: False)()
        for path in paths
    ):
        raise ValueError("Каталог или настройки плагина не должны быть ссылками")
    return root


def environment(home):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(
            ("HERMES_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "UV_")
        ) or key in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PSMODULEPATH"}:
            env.pop(key, None)
    env.update(
        HERMES_HOME=str(home),
        HERMES_DISABLE_LAZY_INSTALLS="1",
        UV_PYTHON_INSTALL_DIR=str(home / "python"),
        UV_CACHE_DIR=str(home / "uv-cache"),
        UV_NO_CONFIG="1",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        NO_PROXY="127.0.0.1,localhost,::1",
        no_proxy="127.0.0.1,localhost,::1",
    )
    return env


def prepare(root, opener=urllib.request.urlopen):
    root = guard_root(root)
    root.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        URL, headers={"User-Agent": "LocalizationStudio-Hermes"}
    )
    with opener(request, timeout=30) as response:
        content = response.read(1_000_001)
    if hashlib.sha256(content).hexdigest() != INSTALLER_HASH:
        raise ValueError("Установщик Hermes изменён или повреждён; установка отменена")
    path = root / "install.ps1"
    path.write_bytes(content)
    return path


def install_commands(root):
    home = root / "home"
    for stage, message in STAGES:
        if stage == "python-deps":
            yield message, [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(RESOURCES / "prepare_hermes.ps1"),
                "-Installer",
                str(root / "install.ps1"),
                "-HermesRoot",
                str(home),
            ]
            continue
        yield message, [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "install.ps1"),
            "-Stage",
            stage,
            "-HermesHome",
            str(home),
            "-InstallDir",
            str(home / "hermes-agent"),
            "-Commit",
            COMMIT,
            "-NonInteractive",
            "-SkipBrowser",
            "-SkipComputerUse",
        ]


def runner_command(root, request):
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(RESOURCES / "run_hermes.ps1"),
        "-Installer",
        str(root / "install.ps1"),
        "-HermesRoot",
        str(root / "home"),
        "-Runner",
        str(RESOURCES / "hermes_bridge.py"),
        "-RequestFile",
        str(request),
    ]


def stop_owned(process):
    if process and process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
        process.wait(timeout=20)
