"""Transactional replacement of application binaries; never removes project data."""

import json
import shutil
from pathlib import Path
from update_package import ROOT_FILES, validate_bundle, reject_links
from update_feed import version_tuple


def read_plan(path):
    path = Path(path).resolve()
    plan = json.loads(path.read_text(encoding="utf-8"))
    home = Path(plan["home"]).resolve()
    version_tuple(plan["version"])
    if (
        not isinstance(plan["gui_pid"], int)
        or plan["gui_pid"] <= 0
        or any(not isinstance(jid, int) or jid <= 0 for jid in plan["resume_jobs"])
    ):
        raise ValueError("Некорректные процессы или очереди в плане обновления")
    session = path.parent
    if home.is_symlink() or not session.is_relative_to(home / "data/updates"):
        raise ValueError("План обновления находится вне каталога приложения")
    if not Path(plan["database"]).resolve().is_relative_to(home / "data"):
        raise ValueError(
            "Обновление разрешено только для базы установленного приложения"
        )
    bundle = Path(plan["bundle"]).resolve()
    backup = Path(plan["backup"]).resolve()
    if bundle != session / "bundle" or not backup.is_relative_to(home / "data/backups"):
        raise ValueError("Недопустимые пути плана обновления")
    for location in [home / "_internal", session, backup, bundle]:
        reject_links(location)
    plan["_path"] = str(path)
    return plan


def replace_application(plan):
    home = Path(plan["home"])
    bundle = Path(plan["bundle"])
    backup = Path(plan["backup"])
    manifest = validate_bundle(bundle, plan["version"])
    backup.mkdir(parents=True, exist_ok=True)
    runtime = backup / "application"
    runtime.mkdir(exist_ok=False)
    names = sorted(
        (set(ROOT_FILES) - {"release-manifest.json"}) & set(manifest["files"])
    ) + ["release-manifest.json", "_internal", "worker_version.txt", "mcp_config.json"]
    saved = []
    installed = []
    try:
        for name in names:
            source = home / name
            if source.is_symlink():
                raise ValueError("Файл приложения является ссылкой")
            if source.exists():
                source.replace(runtime / name)
                saved.append(name)
        for name in sorted(set(ROOT_FILES) & set(manifest["files"])) + [
            "release-manifest.json",
            "_internal",
        ]:
            source = bundle / name
            if name == "release-manifest.json" and name in installed:
                continue
            installed.append(name)
            if source.is_dir():
                shutil.copytree(source, home / name)
            else:
                shutil.copy2(source, home / name)
        installed.append("worker_version.txt")
        (home / "worker_version.txt").write_text(
            "LocalizationWorker.exe", encoding="utf-8"
        )
        original = runtime / "mcp_config.json"
        if original.exists():
            config = json.loads(original.read_text(encoding="utf-8-sig"))
            server = config.get("mcpServers", {}).get("localization-studio")
            if server is not None:
                server["command"] = str(home / "LocalizationWorker.exe")
            installed.append("mcp_config.json")
            (home / "mcp_config.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception:
        # Targets are a fixed allowlist under a validated application root.
        for name in reversed(installed):
            target = home / name
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        for name in reversed(saved):
            (runtime / name).replace(home / name)
        raise
    return backup
