"""Explicit translation of preserved originals, with atomic queue accounting."""

import json, time
from core import dump, now, validate


def active_job(store, pid):
    with store.db() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE project=? AND state IN ('queued','running','paused') ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        return dict(row) if row else None


def submit(store, data):
    pid = int(data["project"])
    rid = int(data["id"])
    revision = int(data["revision"])
    mode = data["mode"]
    if mode not in {"end", "front", "manual"}:
        raise ValueError("Неизвестный способ перевода")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        r = db.execute(
            "SELECT r.* FROM records r JOIN files f ON f.id=r.file JOIN projects p ON p.id=f.project WHERE r.id=? AND f.project=? AND p.deleting=0",
            (rid, pid),
        ).fetchone()
        saved = db.execute(
            "SELECT * FROM preserved_records WHERE record=?", (rid,)
        ).fetchone()
        if (
            not r
            or r["revision"] != revision
            or r["status"] != "preserved"
            or not saved
        ):
            raise ValueError(
                "Строка изменилась или уже покинула эту папку; обновите её"
            )
        old = db.execute(
            "SELECT * FROM revision_snapshots WHERE record=? AND revision=?",
            (rid, saved["previous_revision"]),
        ).fetchone()
        if mode == "manual":
            validate(r["source"], data.get("text"))
        elif r["manual"] or (old and old["manual"]):
            raise ValueError("Ручная правка защищена. Используйте ручной перевод")
        job = db.execute(
            "SELECT * FROM jobs WHERE project=? AND state IN ('queued','running','paused') ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        if mode != "manual" and job and job["stage"] != "translate":
            raise ValueError(
                "Сейчас активна редактура или проверка. Завершите её перед добавлением перевода"
            )
        if mode != "manual" and not job and not data.get("_new_job_validated"):
            raise ValueError(
                "Текущая задача завершилась. Повторите отправку с выбранной моделью"
            )
        if job and job["state"] == "running" and job["current"] == rid:
            raise ValueError("Дождитесь завершения текущей обработки этой строки")
        db.execute(
            "INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)",
            (rid, revision, r["text"], r["status"], r["manual"], r["reviewer"]),
        )
        text = data["text"] if mode == "manual" else old["text"] if old else ""
        status = "translated" if mode == "manual" else "empty"
        reviewer = "human" if mode == "manual" else ""
        db.execute("DELETE FROM preserved_records WHERE record=?", (rid,))
        db.execute(
            "UPDATE records SET text=?,status=?,manual=?,reviewer=?,preserve_override=1,revision=revision+1 WHERE id=?",
            (text, status, int(mode == "manual"), reviewer, rid),
        )
        db.execute(
            "INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)",
            (
                rid,
                revision + 1,
                text,
                status,
                reviewer,
                (
                    "Ручной перевод"
                    if mode == "manual"
                    else "Явно отправлено на перевод: " + mode
                ),
                now(),
            ),
        )
        db.execute("UPDATE errors SET resolved=1 WHERE record=?", (rid,))
        if mode == "manual":
            pending = db.execute(
                "SELECT job FROM queue WHERE record=? AND state='pending'", (rid,)
            ).fetchall()
            db.execute(
                "UPDATE queue SET state='protected' WHERE record=? AND state='pending'",
                (rid,),
            )
            for item in pending:
                db.execute("UPDATE jobs SET done=done+1 WHERE id=?", (item[0],))
            return {"manual": True, "revision": revision + 1}
        created = not job
        if created:
            settings = dict(data["settings"])
            settings.setdefault("cache_namespace", f"project:{pid}")
            jid = store.next_id(db, "jobs")
            db.execute(
                "INSERT INTO jobs(id,project,stage,provider,settings,state,total,created) VALUES (?,?,?,?,?,?,?,?)",
                (
                    jid,
                    pid,
                    "translate",
                    data["provider"],
                    dump(settings),
                    "queued",
                    0,
                    now(),
                ),
            )
        else:
            jid = job["id"]
        edge = db.execute(
            "SELECT coalesce(min(priority),0),coalesce(max(priority),0) FROM queue WHERE job=?",
            (jid,),
        ).fetchone()
        priority = edge[0] - 1 if mode == "front" else edge[1] + 1
        prior = db.execute(
            "SELECT state FROM queue WHERE job=? AND record=?", (jid, rid)
        ).fetchone()
        if prior:
            db.execute(
                "UPDATE queue SET state='pending',finished_at=0,priority=? WHERE job=? AND record=?",
                (priority, jid, rid),
            )
            if prior[0] != "pending":
                db.execute("UPDATE jobs SET done=max(0,done-1) WHERE id=?", (jid,))
        else:
            db.execute(
                "INSERT INTO queue(job,record,priority) VALUES (?,?,?)",
                (jid, rid, priority),
            )
            db.execute("UPDATE jobs SET total=total+1 WHERE id=?", (jid,))
        return {
            "job": jid,
            "created": created,
            "paused": bool(job and job["state"] == "paused"),
            "placement": mode,
        }
