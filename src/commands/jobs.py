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
    if getattr(store, "hermes_inference", False):
        raise ValueError("Сначала завершите или остановите работу Hermes")
    pid = int(data["project"])
    settings = data["settings"]
    with store.db() as db:
        old = json.loads(
            db.execute("SELECT settings FROM projects WHERE id=?", (pid,)).fetchone()[0]
        )
    settings = validate_job_settings(store, pid, data["provider"], dict(settings), old)
    if data["stage"] == "analyze" and data["provider"] == "mcp":
        raise ValueError("Для анализа выберите локальную модель или облачный API")
    analysis_job = None
    if data["stage"] == "translate" and settings.get("analyze_before_translate"):
        if data["provider"] == "mcp":
            raise ValueError(
                "Для предварительного анализа выберите локальную модель или облачный API"
            )
        analysis_job = store.create_job(pid, "analyze", data["provider"], settings)
        settings["_analysis_job"] = analysis_job
    try:
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
            defer=analysis_job is not None,
        )
    except Exception:
        if analysis_job:
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='cancelled' WHERE id=? AND state='queued'",
                    (analysis_job,),
                )
        raise
    with store.db() as db:
        if data.get("record_overrides") is not None:
            from error_workflow import save_overrides

            save_overrides(
                db, jid, data.get("record_ids", []), data["record_overrides"]
            )
        if data.get("persist_settings", True):
            db.execute(
                "UPDATE projects SET settings=? WHERE id=?",
                (
                    dump({k: v for k, v in settings.items() if k != "_analysis_job"}),
                    pid,
                ),
            )
    try:
        job_runtime.launch(store, analysis_job or jid)
    except Exception:
        with store.db() as db:
            db.execute(
                "UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=?",
                (analysis_job or jid,),
            )
        raise ValueError("Не удалось запустить обработчик; задача сохранена")
    return {
        "job": analysis_job or jid,
        "translation_job": jid if analysis_job else None,
    }


def control(store, data):
    if data.get("mode") == "resume" and getattr(store, "hermes_inference", False):
        raise ValueError("Сначала завершите или остановите работу Hermes")
    from worker_supervision import recover_workers

    recover_workers(store)
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
            if job["worker_active"]:
                raise ValueError(
                    "Дождитесь завершения текущего запроса перед продолжением"
                )
            settings = json.loads(job["settings"])
            dependency = settings.get("_analysis_job")
            if dependency:
                prior = db.execute(
                    "SELECT state FROM jobs WHERE id=? AND project=?",
                    (dependency, job["project"]),
                ).fetchone()
                if not prior or prior[0] != "done":
                    raise ValueError(
                        "Сначала завершите предварительный анализ проекта; при его отмене отмените ожидающий перевод и запустите новую цепочку"
                    )
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
