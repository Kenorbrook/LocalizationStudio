from __future__ import annotations
import json, time
from serialization import now, dump
from storage.context_reader import ContextReader


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
    ):
        if stage not in {"translate", "review", "cloud"} or provider not in {
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
        if not (mark_kind and allow_manual):
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
        else:
            query += " AND r.status IN " + (
                "('empty')"
                if stage == "translate"
                else "('translated','edited')" if stage == "cloud" else "('translated')"
            )
        if retry:
            query += " AND EXISTS(SELECT 1 FROM errors e WHERE e.record=r.id AND e.resolved=0)"
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT id FROM projects WHERE id=? AND deleting=0", (project,)
            ).fetchone():
                raise ValueError("Проект удалён или удаляется")
            active = db.execute(
                "SELECT id FROM jobs WHERE project=? AND (state IN ('queued','running','paused','waiting','held') OR worker_active=1)",
                (project,),
            ).fetchone()
            if active and not defer:
                raise ValueError("Завершите или отмените текущую задачу перед новой")
            ids = [
                r[0] for r in db.execute(query + " ORDER BY r.file,r.position", args)
            ]
            if not ids:
                raise ValueError("Нет подходящих строк (ручные правки защищены)")
            settings.setdefault("cache_namespace", f"project:{project}")
            jid = self.database.next_id(db, "jobs")
            db.execute(
                "INSERT INTO jobs(id,project,stage,provider,settings,state,total,created) VALUES (?,?,?,?,?,?,?,?)",
                (
                    jid,
                    project,
                    stage,
                    provider,
                    dump(settings),
                    "waiting" if defer else "queued",
                    len(ids),
                    now(),
                ),
            )
            db.executemany(
                "INSERT INTO queue(job,record) VALUES (?,?)", [(jid, r) for r in ids]
            )
            return jid

    def error(self, job, record, message):
        with self.database.connect() as db:
            p = db.execute("SELECT project FROM jobs WHERE id=?", (job,)).fetchone()[0]
            db.execute(
                "INSERT INTO errors(project,job,record,message,at) VALUES (?,?,?,?,?)",
                (p, job, record, message[:2000], now()),
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
