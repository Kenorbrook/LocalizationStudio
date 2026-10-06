"""Settings command handlers."""

import json
from core import dump
from providers import secret


from speaker_context import neighbor_settings


def process_settings(store, data):
    from process_view import save_preferences

    return save_preferences(store, int(data["project"]), data["preferences"])


def language_preview(store, data):
    from foreign_language import detect

    if not isinstance(data.get("text"), str) or len(data["text"]) > 10000:
        raise ValueError("Для предпросмотра нужно до 10000 символов")
    return detect(data["text"], data.get("source_language", "English"))


def speaker_profiles(store, data):
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or len(profiles) > 500:
        raise ValueError("Профили должны быть объектом с идентификаторами говорящих")
    for key, p in profiles.items():
        if not key.strip() or not isinstance(p, dict):
            raise ValueError("Укажите идентификатор и поля персонажа")
        if any(not isinstance(value, str) for value in p.values()):
            raise ValueError("Поля профиля должны содержать текст")
    if len(dump(profiles)) > 100000:
        raise ValueError("Профили слишком большие")
    with store.db() as db:
        row = db.execute(
            "SELECT settings FROM projects WHERE id=?", (int(data["project"]),)
        ).fetchone()
        if not row:
            raise ValueError("Проект не найден")
        settings = json.loads(row[0])
        settings["speaker_profiles"] = profiles
        db.execute(
            "UPDATE projects SET settings=? WHERE id=?",
            (dump(settings), int(data["project"])),
        )
    return {"ok": True}


def settings(store, data):
    pid = int(data["project"])
    settings = {**data["settings"], **neighbor_settings(data["settings"])}
    key = data.get("key")
    if not isinstance(settings.get("auto_foreign", True), bool):
        raise ValueError("Некорректная настройка определения языка")
    with store.db() as db:
        old = json.loads(
            db.execute("SELECT settings FROM projects WHERE id=?", (pid,)).fetchone()[0]
        )
    if "speaker_profiles" in old:
        settings["speaker_profiles"] = old["speaker_profiles"]
    if key:
        secret(pid, key)
    with store.db() as db:
        db.execute("UPDATE projects SET settings=? WHERE id=?", (dump(settings), pid))
    return {"ok": True, "key_saved": bool(secret(pid))}
