"""Jobs command handlers."""

import json, time
from core import dump
import job_runtime
from job_runtime import run_limits, validate_job_settings
from speaker_context import neighbor_settings


def enqueue_error_retry(store, data):
    from job_chain import enqueue_retry
    from contextlib import nullcontext

    with getattr(store, "launch_lock", nullcontext()):
        return enqueue_retry(store, data)


def error_retry_plan(store, data):
    from job_chain import plan

    return plan(store, int(data["project"]))


def error_retry_budget(store, data):
    from job_chain import bulk_budget

    return bulk_budget(store, int(data["project"]), data["stage"], data["settings"])


def error_budget(store, data):
    from error_workflow import budget

    return budget(
        store,
        int(data["project"]),
        int(data["record"]),
        data["stage"],
        data["settings"],
    )


def retry_errors(store, data):
    from commands import dispatch
    from error_workflow import retry_errors

    return retry_errors(store, data, dispatch)


def job(store, data):
    pid = int(data["project"])
    settings = data["settings"]
    with store.db() as db:
        old = json.loads(
            db.execute("SELECT settings FROM projects WHERE id=?", (pid,)).fetchone()[0]
        )
    settings = validate_job_settings(store, pid, data["provider"], dict(settings), old)
    jid = store.create_job(
        pid,
        data["stage"],
        data["provider"],
        settings,
        data.get("file"),
        bool(data.get("retry")),
        data.get("mark_kind"),
        bool(data.get("allow_manual")),
        data.get("record_ids"),
    )
    with store.db() as db:
        if data.get("record_overrides") is not None:
            from error_workflow import save_overrides

            save_overrides(
                db, jid, data.get("record_ids", []), data["record_overrides"]
            )
        if data.get("persist_settings", True):
            db.execute(
                "UPDATE projects SET settings=? WHERE id=?", (dump(settings), pid)
            )
    try:
        job_runtime.launch(store, jid)
    except Exception:
        with store.db() as db:
            db.execute(
                "UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=?",
                (jid,),
            )
        raise ValueError("Не удалось запустить обработчик; задача сохранена")
    return {"job": jid}


def control(store, data):
    jid = int(data["id"])
    mode = data["mode"]
    with store.db() as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not job:
            raise ValueError("Задача не найдена")
        if mode == "resume":
            if job["state"] not in {"paused", "held"}:
                raise ValueError("Продолжить можно задачу на паузе")
            if db.execute(
                "SELECT id FROM jobs WHERE project=? AND id<>? AND (state IN ('running','queued','paused') OR worker_active=1)",
                (job["project"], jid),
            ).fetchone():
                raise ValueError("Сначала завершите текущую задачу проекта")
            if time.time() - job["heartbeat"] < 10:
                raise ValueError(
                    "Дождитесь завершения текущего запроса перед продолжением"
                )
            settings = json.loads(job["settings"])
            from error_workflow import resume_settings, requeue

            settings = resume_settings(settings, data.get("settings", {}))
            if data.get("retry_records"):
                requeue(db, job, data["retry_records"])
            if data.get("record_overrides") is not None:
                from error_workflow import save_overrides

                save_overrides(
                    db, jid, data.get("retry_records", []), data["record_overrides"]
                )
            settings.update(run_limits(data.get("limits", settings)))
            settings.update(neighbor_settings(data.get("neighbors", settings)))
            if "auto_foreign" in data:
                if not isinstance(data["auto_foreign"], bool):
                    raise ValueError("Некорректная настройка определения языка")
                settings["auto_foreign"] = data["auto_foreign"]
            db.execute("UPDATE jobs SET settings=? WHERE id=?", (dump(settings), jid))
            if not data.get("preserve_project_settings"):
                saved = json.loads(
                    db.execute(
                        "SELECT settings FROM projects WHERE id=?", (job["project"],)
                    ).fetchone()[0]
                )
                saved.update(
                    {
                        k: v
                        for k, v in settings.items()
                        if not k.startswith("_")
                        and k not in {"run_lines", "run_minutes"}
                    }
                )
                db.execute(
                    "UPDATE projects SET settings=? WHERE id=?",
                    (dump(saved), job["project"]),
                )
            db.execute("UPDATE jobs SET state='queued' WHERE id=?", (jid,))
            db.execute(
                "UPDATE jobs SET state='waiting' WHERE project=? AND state='held'",
                (job["project"],),
            )
        elif mode in {"pause", "cancel"}:
            db.execute(
                "UPDATE jobs SET state=? WHERE id=?",
                ("paused" if mode == "pause" else "cancelled", jid),
            )
        else:
            raise ValueError("Некорректная команда")
    if mode == "resume":
        store.classify_preserved(job["project"])
        job_runtime.launch(store, jid)
    return {"ok": True}
