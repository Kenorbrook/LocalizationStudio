"""Job settings validation and worker process launch."""

import json, math, subprocess, sys
from pathlib import Path
from core import APP_HOME, ROOT
from providers import secret


def run_limits(settings):
    result = {}
    for key, maximum in [("run_lines", 1000000), ("run_minutes", 10080)]:
        value = float(settings.get(key, 0) or 0)
        if not 0 <= value <= maximum or not math.isfinite(value):
            raise ValueError("Недопустимый лимит запуска")
        if key == "run_lines" and value != int(value):
            raise ValueError("Число строк должно быть целым")
        result[key] = int(value) if key == "run_lines" else value
    return result


def launch(store, jid):
    from contextlib import nullcontext

    with getattr(store, "launch_lock", nullcontext()):
        if getattr(store, "closing", False):
            raise ValueError("Приложение закрывается; очередь сохранена")
        return _launch(store, jid)


def _launch(store, jid):
    if getattr(sys, "frozen", False):
        executable = APP_HOME / "LocalizationWorker.exe"
        pointer = APP_HOME / "worker_version.txt"
        if pointer.is_file():
            name = pointer.read_text(encoding="utf-8-sig").strip()
            if (
                Path(name).name == name
                and name.startswith("LocalizationWorker_")
                and name.endswith(".exe")
                and (APP_HOME / name).is_file()
            ):
                executable = APP_HOME / name
        args = [str(executable)]
    else:
        args = [sys.executable, str(ROOT / "app.py")]
    args += ["--worker", str(jid), "--db", str(store.path)]
    folder = store.path.parent / "worker-logs"
    folder.mkdir(parents=True, exist_ok=True)
    with store.db() as db:
        db.execute("UPDATE jobs SET worker_active=1 WHERE id=?", (jid,))
    try:
        with (folder / f"job-{jid}-process.log").open("ab") as output:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception:
        with store.db() as db:
            db.execute("UPDATE jobs SET worker_active=0 WHERE id=?", (jid,))
        raise
    with store.db() as db:
        db.execute("UPDATE jobs SET pid=? WHERE id=?", (process.pid, jid))


def validate_job_settings(store, pid, provider, settings, old):
    if provider not in {"local", "cloud", "mcp"}:
        raise ValueError("Некорректный провайдер")
    if not isinstance(settings.get("auto_foreign", True), bool):
        raise ValueError("Некорректная настройка определения языка")
    if "speaker_profiles" in old:
        settings["speaker_profiles"] = old["speaker_profiles"]
    if len(str(settings.get("review_instruction", ""))) > 4000:
        raise ValueError("Инструкция редактору слишком длинная")
    settings.update(run_limits(settings))
    for key, default, minimum, maximum in [
        ("context", 8192, 1024, 131072),
        ("max_calls", 1000, 1, 1000000),
        ("max_output", 1200, 100, 100000),
    ]:
        value = int(settings.get(key, default))
        if not minimum <= value <= maximum:
            raise ValueError("Недопустимая настройка: " + key)
        settings[key] = value
    if (
        (provider == "local" and not settings.get("model"))
        or not settings.get("source_language")
        or not settings.get("target_language")
    ):
        raise ValueError("Выберите модель и языки")
    if provider == "mcp":
        from mcp_server import connections

        if not any(
            c["session"] == settings.get("mcp_session") and c["sampling"]
            for c in connections(store)
        ):
            raise ValueError(
                "Выбранный MCP-клиент не подключён или не поддерживает генерацию (sampling)"
            )
    if provider == "cloud":
        if not secret(pid):
            raise ValueError("Сначала сохраните API-ключ")
        if not settings.get("endpoint") or not settings.get("cloud_model"):
            raise ValueError("Задайте API URL и облачную модель")
    return settings
