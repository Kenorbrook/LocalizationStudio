"""Records command handlers."""

import json
from core import dump
import job_runtime
from job_runtime import validate_job_settings
from speaker_context import neighbor_settings


def manual_error(store, data):
    from commands import dispatch

    row = store.record(int(data["id"]))
    if row["project"] != int(data["project"]):
        raise ValueError("Строка не принадлежит проекту")
    if row["preserve_kind"]:
        return dispatch(store, "translate-preserved", {**data, "mode": "manual"})
    return store.update(
        row["id"],
        int(data["revision"]),
        data["text"],
        "translated",
        "human",
        "Ручной перевод ошибки",
        manual=True,
    )


def translate_preserved(store, data):
    from preserved_workflow import active_job, submit
    from contextlib import nullcontext

    with getattr(store, "launch_lock", nullcontext()):
        if getattr(store, "closing", False):
            raise ValueError("Приложение закрывается")
        payload = dict(data)
        pid = int(data["project"])
        if data["mode"] != "manual" and not active_job(store, pid):
            with store.db() as db:
                row = db.execute(
                    "SELECT settings FROM projects WHERE id=?", (pid,)
                ).fetchone()
                if not row:
                    raise ValueError("Проект не найден")
            payload["settings"] = validate_job_settings(
                store, pid, data["provider"], dict(data["settings"]), json.loads(row[0])
            )
            payload["settings"].update(neighbor_settings(payload["settings"]))
            payload["_new_job_validated"] = True
        result = submit(store, payload)
        if result.get("created"):
            try:
                job_runtime.launch(store, result["job"])
            except Exception:
                with store.db() as db:
                    db.execute(
                        "UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=?",
                        (result["job"],),
                    )
                raise ValueError(
                    "Не удалось запустить обработчик; строка сохранена в очереди на паузе"
                )
        return result


def preserved_check(store, data):
    from preservation import symbolic
    from foreign_language import detect

    pid = int(data["project"])
    rid = int(data["record"])
    with store.db() as db:
        if not db.execute(
            "SELECT 1 FROM records r JOIN files f ON f.id=r.file WHERE r.id=? AND f.project=? AND r.status='preserved'",
            (rid, pid),
        ).fetchone():
            raise ValueError("Строка не принадлежит этой папке проекта")
    row = store.record(rid)
    if symbolic(row["source"]):
        explanation = "Строка состоит из символов, паузы или служебных вставок. Оригинал можно сохранить без перевода; ручная проверка обычно не требуется."
    else:
        result = detect(row["source"], data.get("source_language", "English"))
        explanation = (
            result.get("reason")
            or "Автоматическая проверка не подтверждает другой язык с достаточной уверенностью. Решение автора или человека может быть верным; посмотрите контекст."
        )
    return {"explanation": explanation, "inference": False, "changed": False}


def preserve(store, data):
    return store.preserve(
        int(data["id"]), int(data["revision"]), data["kind"], data["reason"]
    )


def restore_preserved(store, data):
    return store.restore_preserved(int(data["id"]), int(data["revision"]))


def clear_marks(store, data):
    return store.clear_marks(int(data["project"]), data["kind"])


def mark(store, data):
    return store.mark(int(data["id"]), int(data["revision"]), data["kind"])


def save(store, data):
    return store.update(
        int(data["id"]),
        int(data["revision"]),
        data["text"],
        "verified" if data.get("verified") else "translated",
        "human",
        "Ручная проверка" if data.get("verified") else "Ручная правка",
        manual=True,
    )


def undo(store, data):
    return store.undo(int(data["id"]), int(data["revision"]))


def unlock(store, data):
    with store.db() as db:
        db.execute(
            "UPDATE records SET manual=0,revision=revision+1 WHERE id=?",
            (int(data["id"]),),
        )
    return {"ok": True}


def proposal(store, data):
    with store.db() as db:
        p = dict(
            db.execute(
                "SELECT * FROM proposals WHERE id=? AND state='pending'",
                (int(data["id"]),),
            ).fetchone()
        )
    if data.get("accept"):
        store.update(
            p["record"],
            p["revision"],
            p["text"],
            "verified" if data.get("verified") else "translated",
            "human / MCP:" + p["reviewer"],
            p["reason"],
            manual=True,
        )
    with store.db() as db:
        db.execute(
            "UPDATE proposals SET state=? WHERE id=?",
            ("accepted" if data.get("accept") else "rejected", p["id"]),
        )
    return {"ok": True}
