"""Opt-in bounded retries; no model controls jobs or edits completed translations."""

import json
from collections import defaultdict
from serialization import now, dump

MAX_ROUNDS = 2


def preference(store, project, enabled=None):
    with store.db() as db:
        if not db.execute(
            "SELECT 1 FROM projects WHERE id=? AND deleting=0", (project,)
        ).fetchone():
            raise ValueError("Проект не найден")
        if enabled is not None:
            if type(enabled) is not bool:
                raise ValueError("Некорректная настройка повтора")
            db.execute(
                "INSERT OR REPLACE INTO recovery_preferences VALUES (?,?)",
                (project, int(enabled)),
            )
            if not enabled:
                db.execute(
                    "UPDATE jobs SET state='cancelled',error='Автоповтор отключён' WHERE project=? AND state IN ('waiting','held') AND EXISTS(SELECT 1 FROM recovery_jobs WHERE job=jobs.id)",
                    (project,),
                )
        row = db.execute(
            "SELECT enabled FROM recovery_preferences WHERE project=?", (project,)
        ).fetchone()
        return bool(row and row[0])


def adjustment(settings, message):
    text = message.lower()
    result = dict(settings)
    context = int(settings.get("context", 8192))
    output = int(settings.get("max_output", 1200))
    if any(
        term in text
        for term in [
            "не хватило оперативной памяти",
            "out of memory",
            "bad_alloc",
            "failed to allocate",
        ]
    ):
        if context <= 2048:
            return None
        result.update(
            context=max(2048, context // 2), context_before=0, context_after=0
        )
        result["max_output"] = min(output, max(100, result["context"] // 3))
        reason = "Меньше памяти: уменьшены контекст и соседние реплики"
    elif "лимита ответа" in text or "лимита выходных" in text:
        result["max_output"] = min(output * 2, max(100, context - 2048))
        if result["max_output"] <= output:
            return None
        reason = "Больше места для ответа в пределах прежнего контекста"
    elif any(
        term in text
        for term in [
            "не помещается",
            "не помещаются",
            "превышает бюджет",
            "слишком мало места",
        ]
    ):
        if not int(settings.get("context_before", 0)) and not int(
            settings.get("context_after", 0)
        ):
            return None
        result.update(context_before=0, context_after=0)
        reason = "Убраны соседние реплики; правила и служебные символы сохраняются"
    else:
        return None
    return result, reason


def plan(store, project, job_id=None):
    with store.db() as db:
        if job_id is None:
            row = db.execute(
                "SELECT id FROM jobs WHERE project=? AND provider='local' AND stage='translate' AND state='incomplete' ORDER BY id DESC LIMIT 1",
                (project,),
            ).fetchone()
            job_id = row[0] if row else None
        origin = db.execute(
            "SELECT * FROM jobs WHERE id=? AND project=? AND provider='local' AND stage='translate'",
            (job_id, project),
        ).fetchone()
        if not origin:
            return {"job": None, "rows": [], "round": 0}
        settings = json.loads(origin["settings"])
        round_number = int(settings.get("_recovery_round", 0)) + 1
        result = {"job": origin["id"], "rows": [], "round": round_number}
        if (
            origin["state"] != "incomplete"
            or origin["worker_active"]
            or round_number > MAX_ROUNDS
            or db.execute(
                "SELECT 1 FROM recovery_jobs WHERE parent=?", (job_id,)
            ).fetchone()
        ):
            return result
        errors = db.execute(
            "SELECT e.id,e.record,e.message,r.revision,r.source FROM errors e JOIN records r ON r.id=e.record JOIN files f ON f.id=r.file WHERE e.job=? AND e.project=? AND f.project=? AND e.resolved=0 AND r.manual=0 AND r.status='empty' AND e.id=(SELECT max(id) FROM errors WHERE record=e.record AND job=e.job AND resolved=0) ORDER BY e.id LIMIT 200",
            (job_id, project, project),
        ).fetchall()
        for error in errors:
            custom = db.execute(
                "SELECT settings FROM queue_overrides WHERE job=? AND record=?",
                (job_id, error["record"]),
            ).fetchone()
            effective = {**settings, **(json.loads(custom[0]) if custom else {})}
            changed = adjustment(effective, error["message"])
            if changed:
                selected, reason = changed
                result["rows"].append(
                    {
                        "record": error["record"],
                        "revision": error["revision"],
                        "error": error["id"],
                        "reason": reason,
                        "settings": selected,
                    }
                )
        return result


def apply(store, project, job_id=None, automatic=False):
    from contextlib import nullcontext
    from job_chain import schedule
    from job_runtime import validate_job_settings

    with getattr(store, "launch_lock", nullcontext()):
        if getattr(store, "closing", False):
            return []
        if automatic and not preference(store, project):
            return []
        selected = plan(store, project, job_id)
        if not selected["rows"]:
            return []
        groups = defaultdict(list)
        for row in selected["rows"]:
            settings = row["settings"]
            settings.update(
                _recovery_round=selected["round"],
                _recovery_root=settings.get("_recovery_root", selected["job"]),
                _retry_origin=selected["job"],
            )
            # A retry is a separate bounded run, not a reset of the original queue.
            settings.update(run_lines=0, run_minutes=0)
            validated = validate_job_settings(
                store, project, "local", settings, settings
            )
            groups[dump(validated)].append(row)
        jobs = []
        with store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute(
                "SELECT state,worker_active FROM jobs WHERE id=? AND project=?",
                (selected["job"], project),
            ).fetchone()
            if (
                not parent
                or parent["state"] != "incomplete"
                or parent["worker_active"]
                or db.execute(
                    "SELECT 1 FROM recovery_jobs WHERE parent=?", (selected["job"],)
                ).fetchone()
            ):
                return []
            if db.execute(
                "SELECT 1 FROM jobs WHERE project=? AND (state IN ('paused','held') OR ((state IN ('running','queued') OR worker_active=1) AND ?=0))",
                (project, int(automatic)),
            ).fetchone():
                if automatic:
                    return []
                raise ValueError(
                    "Завершите текущую задачу; повтор не возобновляет паузу автоматически"
                )
            if (
                automatic
                and not db.execute(
                    "SELECT 1 FROM recovery_preferences WHERE project=? AND enabled=1",
                    (project,),
                ).fetchone()
            ):
                return []
            if not db.execute(
                "SELECT 1 FROM projects WHERE id=? AND deleting=0", (project,)
            ).fetchone():
                return []
            for encoded, rows in groups.items():
                valid = [
                    row
                    for row in rows
                    if db.execute(
                        "SELECT 1 FROM records r JOIN files f ON f.id=r.file JOIN errors e ON e.record=r.id WHERE r.id=? AND r.revision=? AND r.manual=0 AND r.status='empty' AND f.project=? AND e.id=? AND e.resolved=0",
                        (row["record"], row["revision"], project, row["error"]),
                    ).fetchone()
                ]
                if not valid:
                    continue
                jid = store.next_id(db, "jobs")
                db.execute(
                    "INSERT INTO jobs(id,project,stage,provider,settings,state,total,created) VALUES (?,?,'translate','local',?,'waiting',?,?)",
                    (jid, project, encoded, len(valid), now()),
                )
                db.executemany(
                    "INSERT INTO queue(job,record) VALUES (?,?)",
                    [(jid, row["record"]) for row in valid],
                )
                db.executemany(
                    "INSERT INTO recovery_jobs(parent,record,job,reason) VALUES (?,?,?,?)",
                    [
                        (selected["job"], row["record"], jid, row["reason"])
                        for row in valid
                    ],
                )
                jobs.append(jid)
        schedule(store, project)
        return jobs


def after_job(store, job):
    with store.db() as db:
        row = db.execute("SELECT project,state FROM jobs WHERE id=?", (job,)).fetchone()
    if row and row["state"] == "incomplete":
        return apply(store, row["project"], job, automatic=True)
    return []
