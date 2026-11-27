from __future__ import annotations
import json, time
from serialization import now, dump
from storage.context_reader import ContextReader

LIVE = (
    "(j.state IN ('queued','running','paused','waiting','held') OR j.worker_active=1)"
)
RESERVED = (
    "NOT EXISTS(SELECT 1 FROM queue other JOIN jobs j ON j.id=other.job "
    "WHERE other.record=r.id AND other.state='pending' AND j.project=f.project "
    "AND j.id<>? AND " + LIVE + ")"
)


def split_tail(rows, count):
    """Prefer whole scenes or files within the requested cap."""
    limit = min(count or (len(rows) + 1) // 2, len(rows))
    start = len(rows) - limit
    while start and start < len(rows) - 1 and rows[start - 1][1:] == rows[start][1:]:
        start += 1
    if not start or rows[start - 1][1:] != rows[start][1:]:
        return rows[start:]
    # The entire requested tail belongs to one scene.
    return rows[-limit:]


class JobsRepository:
    def __init__(self, database, records):
        self.database = database
        self.records = records
        self.context = ContextReader(database, records)

    def create_job(
        self,
        project,
        stage,
        provider,
        settings,
        file=None,
        retry=False,
        mark_kind=None,
        allow_manual=False,
        record_ids=None,
        defer=False,
        *,
        parallel=False,
        split_from=0,
        take=0,
    ):
        if stage not in {"translate", "review", "cloud", "analyze"} or provider not in {
            "local",
            "cloud",
            "mcp",
        }:
            raise ValueError("Некорректный режим")
        if stage == "cloud" and provider not in {"cloud", "mcp"}:
            raise ValueError("Зелёная проверка требует внешнего провайдера")
        from speaker_context import neighbor_settings

        self.records.classify_preserved(project)
        if not isinstance(settings.get("auto_foreign", True), bool):
            raise ValueError("Некорректная настройка определения языка")
        settings = {
            **settings,
            **neighbor_settings(settings),
            "auto_foreign": settings.get("auto_foreign", True),
        }
        settings.pop("marked_review", None)
        settings.pop("review_manual", None)
        if mark_kind is not None and (
            mark_kind not in {"bad", "review"} or stage != "review"
        ):
            raise ValueError("Папки пометок доступны только для редактуры")
        query = (
            "SELECT r.id FROM records r JOIN files f ON f.id=r.file WHERE f.project=?"
        )
        if stage != "analyze" and not (mark_kind and allow_manual):
            query += " AND r.manual=0"
        if mark_kind:
            settings.update(
                marked_review=mark_kind,
                review_manual=bool(allow_manual),
                review_pass=str(time.time_ns()),
            )
        args = [project]
        if record_ids is not None:
            if not record_ids:
                raise ValueError("Нет выбранных строк")
            query += " AND r.id IN (" + ",".join("?" for _ in record_ids) + ")"
            args.extend(record_ids)
        if file:
            query += " AND r.file=?"
            args.append(file)
        if mark_kind:
            query += " AND r.status<>'preserved' AND r.text<>'' AND EXISTS(SELECT 1 FROM record_marks m WHERE m.record=r.id AND m.kind=?)"
            args.append(mark_kind)
        elif stage != "analyze":
            query += " AND r.status IN " + (
                "('empty')"
                if stage == "translate"
                else "('translated','edited')" if stage == "cloud" else "('translated')"
            )
        if retry:
            from error_catalog import VISIBLE

            query += (
                " AND EXISTS(SELECT 1 FROM errors e WHERE e.record=r.id AND e.project=f.project AND e.resolved=0 AND "
                + VISIBLE
                + ")"
            )
        if parallel:
            query += " AND " + RESERVED
            args.append(split_from)
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT id FROM projects WHERE id=? AND deleting=0", (project,)
            ).fetchone():
                raise ValueError("Проект удалён или удаляется")
            active = db.execute(
                "SELECT j.* FROM jobs j WHERE project=? AND " + LIVE,
                (project,),
            ).fetchall()
            if active and not (defer or parallel):
                raise ValueError("Завершите или отмените текущую задачу перед новой")
            if parallel and any(j["stage"] == "analyze" for j in active):
                raise ValueError("Дождитесь завершения анализа проекта")
            if split_from:
                source = next((j for j in active if j["id"] == split_from), None)
                if (
                    not source
                    or source["stage"] != "translate"
                    or source["state"] not in {"running", "paused", "queued"}
                ):
                    raise ValueError("Выберите активную очередь перевода")
                query = query.replace(
                    "SELECT r.id ", "SELECT r.id,r.file,coalesce(r.scene,'') ", 1
                )
                query += (
                    " AND r.id<>coalesce(?,0) "
                    "AND EXISTS(SELECT 1 FROM queue original WHERE original.job=? AND original.record=r.id AND original.state='pending' AND original.retry_through_error=0 AND original.priority=0) "
                    "AND NOT EXISTS(SELECT 1 FROM queue_overrides o WHERE o.job=? AND o.record=r.id)"
                )
                args += [source["current"], split_from, split_from]
                rows = [
                    tuple(r)
                    for r in db.execute(
                        query + " ORDER BY r.file,r.position,r.id", args
                    )
                ]
                pending = db.execute(
                    "SELECT count(*) FROM queue WHERE job=? AND state='pending'",
                    (split_from,),
                ).fetchone()[0]
                cap = min(take or (len(rows) + 1) // 2, pending - 1)
                ids = [r[0] for r in split_tail(rows, cap)] if cap > 0 and rows else []
            else:
                if take:
                    query += " ORDER BY r.file,r.position,r.id LIMIT ?"
                    args.append(take)
                else:
                    query += " ORDER BY r.file,r.position,r.id"
                ids = [r[0] for r in db.execute(query, args)]
            if not ids:
                raise ValueError(
                    "Нет свободных строк для этой очереди"
                    if parallel
                    else "Нет подходящих строк (ручные правки защищены)"
                )
            settings.setdefault("cache_namespace", f"project:{project}")
            jid = self.database.next_id(db, "jobs")
            db.execute(
                "INSERT INTO jobs(id,project,stage,provider,settings,state,total,created,parallel) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    jid,
                    project,
                    stage,
                    provider,
                    dump(settings),
                    "waiting" if defer else "queued",
                    len(ids),
                    now(),
                    int(parallel),
                ),
            )
            db.executemany(
                "INSERT INTO queue(job,record) VALUES (?,?)", [(jid, r) for r in ids]
            )
            if split_from:
                db.execute(
                    "DELETE FROM queue WHERE job=? AND record IN (SELECT record FROM queue WHERE job=?)",
                    (split_from, jid),
                )
                db.execute(
                    "UPDATE jobs SET total=total-?,parallel=1 WHERE id=?",
                    (len(ids), split_from),
                )
            if retry:
                from error_catalog import mark_retry

                for rid in ids:
                    mark_retry(db, project, jid, rid)
            return jid

    def error(self, job, record, message, candidate=""):
        with self.database.connect() as db:
            p = db.execute("SELECT project FROM jobs WHERE id=?", (job,)).fetchone()[0]
            db.execute(
                "INSERT INTO errors(project,job,record,message,at,candidate) VALUES (?,?,?,?,?,?)",
                (
                    p,
                    job,
                    record,
                    message[:2000],
                    now(),
                    (
                        candidate
                        if isinstance(candidate, str) and len(candidate) <= 100000
                        else ""
                    ),
                ),
            )
        if record:
            try:
                from error_workflow import budget

                with self.database.connect() as db:
                    j = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
                    settings = json.loads(j["settings"])
                    override = db.execute(
                        "SELECT settings FROM queue_overrides WHERE job=? AND record=?",
                        (job, record),
                    ).fetchone()
                    if override:
                        settings.update(json.loads(override[0]))
                estimate = budget(self.context, p, record, j["stage"], settings)
                with self.database.connect() as db:
                    db.execute(
                        "UPDATE errors SET budget_json=? WHERE job=? AND record=? AND resolved=0",
                        (dump(estimate), job, record),
                    )
            except (ValueError, KeyError, TypeError):
                pass
