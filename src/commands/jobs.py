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
    if getattr(store, "engine_mutation", None):
        raise ValueError("Дождитесь подготовки или сборки копии игры")
    if getattr(store, "hermes_inference", False):
        raise ValueError("Сначала завершите или остановите работу Hermes")
    pid = int(data["project"])
    settings = data["settings"]
    with store.db() as db:
        old = json.loads(
            db.execute("SELECT settings FROM projects WHERE id=?", (pid,)).fetchone()[0]
        )
    settings = validate_job_settings(store, pid, data["provider"], dict(settings), old)
    from cloud_connections import preserve_legacy

    preserve_legacy(old, settings)
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


def parallel_job(store, data):
    from contextlib import nullcontext

    pid = int(data["project"])
    stage = data["stage"]
    provider = data["provider"]
    split_from = int(data.get("split_from") or 0)
    take = data.get("take", 0)
    if type(take) is not int or take < 0 or take > 1000000:
        raise ValueError("Укажите количество строк от 0 до 1 000 000")
    if stage not in {"translate", "review", "cloud"} or (
        split_from and stage != "translate"
    ):
        raise ValueError("Некорректный этап параллельной задачи")
    with getattr(store, "launch_lock", nullcontext()):
        if getattr(store, "engine_mutation", None):
            raise ValueError("Дождитесь подготовки или сборки копии игры")
        if getattr(store, "hermes_inference", False) or getattr(
            store, "closing", False
        ):
            raise ValueError("Сейчас нельзя запустить обработчик")
        with store.db() as db:
            row = db.execute(
                "SELECT settings FROM projects WHERE id=? AND deleting=0", (pid,)
            ).fetchone()
        if not row:
            raise ValueError("Проект не найден")
        options = {k: v for k, v in data["settings"].items() if not k.startswith("_")}
        options["analyze_before_translate"] = False
        settings = validate_job_settings(
            store, pid, provider, options, json.loads(row[0])
        )
        jid = store.create_job(
            pid,
            stage,
            provider,
            settings,
            file=data.get("file"),
            parallel=True,
            split_from=split_from,
            take=take,
        )
        try:
            job_runtime.launch(store, jid)
        except Exception:
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=? AND state='queued'",
                    (jid,),
                )
            raise ValueError(
                "Не удалось запустить обработчик; задача сохранена"
            ) from None
    return {"job": jid}


def queue_settings(store, data):
    jid = int(data["id"])
    with store.db() as db:
        row = db.execute(
            "SELECT j.* FROM jobs j JOIN projects p ON p.id=j.project WHERE j.id=? AND p.deleting=0",
            (jid,),
        ).fetchone()
    if (
        not row
        or row["state"] not in {"paused", "waiting", "held"}
        or row["worker_active"]
    ):
        raise ValueError("Приостановите задачу и дождитесь остановки обработчика")
    provider = data["provider"]
    if (
        row["stage"] == "cloud"
        and provider not in {"cloud", "mcp"}
        or row["stage"] == "analyze"
        and provider == "mcp"
    ):
        raise ValueError("Этот исполнитель недоступен для этапа")
    old = json.loads(row["settings"])
    from cloud_connections import preserve_legacy

    options = {
        key: value for key, value in data["settings"].items() if not key.startswith("_")
    }
    if (
        provider == "cloud"
        and not options.get("cloud_connection", old.get("cloud_connection"))
        and (
            row["provider"] != "cloud"
            or options.get("endpoint", old.get("endpoint")) != old.get("endpoint")
        )
    ):
        raise ValueError("Выберите API-ключ для этого адреса")
    settings = validate_job_settings(
        store, row["project"], provider, {**old, **options}, old
    )
    settings.update(neighbor_settings(settings))
    preserve_legacy(old, settings)
    settings["_queue_configured"] = True
    with store.db() as db:
        changed = db.execute(
            "UPDATE jobs SET settings=?,provider=? WHERE id=? AND settings=? AND provider=? "
            "AND state IN ('paused','waiting','held') AND worker_active=0 "
            "AND EXISTS(SELECT 1 FROM projects WHERE id=jobs.project AND deleting=0)",
            (dump(settings), provider, jid, row["settings"], row["provider"]),
        )
        if not changed.rowcount:
            raise ValueError("Очередь изменилась. Откройте настройки заново")
    return {"ok": True}


def queue_priority(store, data):
    jid = int(data["id"])
    direction = data["direction"]
    if direction not in ("up", "down"):
        raise ValueError("Некорректное направление")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT project FROM jobs WHERE id=? AND state='waiting'", (jid,)
        ).fetchone()
        if not row:
            raise ValueError("Приоритет можно менять у ожидающей очереди")
        waiting = [
            r[0]
            for r in db.execute(
                "SELECT id FROM jobs WHERE project=? AND state='waiting' ORDER BY queue_order,id",
                (row["project"],),
            )
        ]
        position = waiting.index(jid)
        other = position + (-1 if direction == "up" else 1)
        if 0 <= other < len(waiting):
            waiting[position], waiting[other] = waiting[other], waiting[position]
            db.executemany(
                "UPDATE jobs SET queue_order=? WHERE id=?",
                enumerate(waiting, -len(waiting)),
            )
    return {"ok": True}


def control(store, data):
    if data.get("mode") in {"resume", "start"} and getattr(
        store, "hermes_inference", False
    ):
        raise ValueError("Сначала завершите или остановите работу Hermes")
    from worker_supervision import recover_workers

    recover_workers(store)
    jid = int(data["id"])
    mode = data["mode"]
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not job:
            raise ValueError("Задача не найдена")
        if mode in {"resume", "start"}:
            if mode == "start" and job["state"] != "waiting":
                raise ValueError("Выберите ожидающую очередь")
            if mode == "resume" and job["state"] not in {"paused", "held"}:
                raise ValueError("Продолжить можно задачу на паузе")
            peers = db.execute(
                "SELECT id,parallel,stage FROM jobs WHERE project=? AND id<>? AND (state IN ('running','queued','paused') OR worker_active=1)",
                (job["project"], jid),
            ).fetchall()
            if any(
                "analyze" in (job["stage"], peer["stage"])
                or not (mode == "start" or job["parallel"] or peer["parallel"])
                for peer in peers
            ):
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
            if (
                peers
                and db.execute(
                    "SELECT 1 FROM queue q JOIN queue other ON other.record=q.record "
                    "JOIN jobs j ON j.id=other.job WHERE q.job=? AND q.state='pending' "
                    "AND other.job<>? AND other.state='pending' AND j.project=? "
                    "AND (j.state IN ('running','queued','paused') OR j.worker_active=1) LIMIT 1",
                    (jid, jid, job["project"]),
                ).fetchone()
            ):
                raise ValueError("Часть строк уже принадлежит другой активной очереди")
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
                from cloud_connections import preserve_legacy

                updated = {
                    k: v
                    for k, v in settings.items()
                    if not k.startswith("_") and k not in {"run_lines", "run_minutes"}
                }
                preserve_legacy(saved, updated)
                saved.update(updated)
                db.execute(
                    "UPDATE projects SET settings=? WHERE id=?",
                    (dump(saved), job["project"]),
                )
            db.execute(
                "UPDATE jobs SET state='queued',parallel=CASE WHEN ? THEN 1 ELSE parallel END WHERE id=?",
                (mode == "start", jid),
            )
            db.execute(
                "UPDATE jobs SET state='waiting' WHERE project=? AND state='held'",
                (job["project"],),
            )
        elif mode in {"pause", "cancel"}:
            db.execute(
                "UPDATE jobs SET state=?,error=CASE WHEN retry_at>0 THEN '' ELSE error END,retry_at=0 WHERE id=?",
                ("paused" if mode == "pause" else "cancelled", jid),
            )
        else:
            raise ValueError("Некорректная команда")
    if mode in {"resume", "start"}:
        try:
            store.classify_preserved(job["project"])
            job_runtime.launch(store, jid)
        except Exception:
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=? AND state='queued' AND worker_active=0",
                    (jid,),
                )
            raise
    return {"ok": True}
