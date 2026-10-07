from __future__ import annotations
import json, time
from serialization import now
from validation import validate


class RecordsRepository:
    def __init__(self, database):
        self.database = database

    def record(self, rid):
        with self.database.connect() as db:
            r = db.execute(
                "SELECT r.*,f.project,f.path,coalesce(m.kind,'') flag,m.at flagged_at,m.was_manual flag_previous_manual,coalesce((SELECT reason FROM preserved_records WHERE record=r.id),'') preserve_reason,coalesce((SELECT kind FROM preserved_records WHERE record=r.id),'') preserve_kind FROM records r JOIN files f ON f.id=r.file LEFT JOIN record_marks m ON m.record=r.id WHERE r.id=?",
                (rid,),
            ).fetchone()
            if not r:
                raise ValueError("Строка не найдена")
            result = dict(r)
            check = db.execute(
                "SELECT result FROM language_checks WHERE record=?", (rid,)
            ).fetchone()
            result["language_note"] = (
                json.loads(check[0]).get("reason", "") if check else ""
            )
            return result

    def preserve(self, rid, revision, kind, reason, worker_job=None):
        if (
            kind not in {"symbols", "foreign"}
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 1000
        ):
            raise ValueError("Укажите причину сохранения оригинала (до 1000 символов)")
        with self.database.connect() as db:
            r = db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
            if not r or r["revision"] != revision:
                raise ValueError("Строка изменилась; обновите её")
            if not r["source"].strip():
                raise ValueError("Пустая строка")
            if db.execute(
                "SELECT 1 FROM jobs j JOIN queue q ON q.job=j.id WHERE j.state='running' AND j.current=? AND q.record=? AND q.state='pending' AND j.id<>?",
                (rid, rid, worker_job or -1),
            ).fetchone():
                raise ValueError("Дождитесь завершения обработки этой строки")
            if (
                worker_job
                and not db.execute(
                    "SELECT 1 FROM jobs WHERE id=? AND current=? AND state='running'",
                    (worker_job, rid),
                ).fetchone()
            ):
                raise ValueError("Обработчик больше не владеет строкой")
            prior = db.execute(
                "SELECT previous_revision FROM preserved_records WHERE record=?", (rid,)
            ).fetchone()
            db.execute(
                "INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)",
                (rid, revision, r["text"], r["status"], r["manual"], r["reviewer"]),
            )
            db.execute(
                "INSERT OR REPLACE INTO preserved_records(record,kind,reason,previous_revision,at) VALUES (?,?,?,?,?)",
                (
                    rid,
                    kind,
                    reason.strip(),
                    prior[0] if prior else revision,
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE records SET text=source,status='preserved',revision=revision+1,reviewer='literal' WHERE id=?",
                (rid,),
            )
            db.execute(
                "INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)",
                (
                    rid,
                    revision + 1,
                    r["source"],
                    "preserved",
                    "literal",
                    reason.strip(),
                    now(),
                ),
            )
            pending = db.execute(
                "SELECT job FROM queue WHERE record=? AND state='pending' AND job<>?",
                (rid, worker_job or -1),
            ).fetchall()
            db.execute(
                "UPDATE queue SET state='done' WHERE record=? AND state='pending' AND job<>?",
                (rid, worker_job or -1),
            )
            for job in pending:
                db.execute("UPDATE jobs SET done=done+1 WHERE id=?", (job[0],))
            db.execute("UPDATE errors SET resolved=1 WHERE record=?", (rid,))
        return self.record(rid)

    def restore_preserved(self, rid, revision):
        with self.database.connect() as db:
            r = db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
            p = db.execute(
                "SELECT * FROM preserved_records WHERE record=?", (rid,)
            ).fetchone()
            if not r or r["revision"] != revision or not p:
                raise ValueError("Строка изменилась или уже возвращена")
            old = db.execute(
                "SELECT * FROM revision_snapshots WHERE record=? AND revision=?",
                (rid, p["previous_revision"]),
            ).fetchone()
            db.execute("DELETE FROM preserved_records WHERE record=?", (rid,))
            text, status, manual, reviewer = (
                (old["text"], old["status"], old["manual"], old["reviewer"])
                if old
                else ("", "empty", r["manual"], "")
            )
            if r["manual"] and r["text"] != r["source"]:
                text, status, manual, reviewer = (
                    r["text"],
                    "translated",
                    1,
                    r["reviewer"],
                )
            db.execute(
                "UPDATE records SET text=?,status=?,manual=?,reviewer=?,preserve_override=1,revision=revision+1 WHERE id=?",
                (text, status, manual, reviewer, rid),
            )
            if status == "empty":
                jobs = db.execute(
                    "SELECT j.id FROM jobs j JOIN queue q ON q.job=j.id WHERE q.record=? AND q.state='done' AND j.stage='translate' AND j.state IN ('queued','running','paused')",
                    (rid,),
                ).fetchall()
                for job in jobs:
                    db.execute(
                        "UPDATE queue SET state='pending',finished_at=0 WHERE job=? AND record=?",
                        (job[0], rid),
                    )
                    db.execute(
                        "UPDATE jobs SET done=max(0,done-1) WHERE id=?", (job[0],)
                    )

        return self.record(rid)

    def classify_preserved(self, project=None):
        from preservation import symbolic

        with self.database.connect() as db:
            query = "SELECT r.* FROM records r JOIN files f ON f.id=r.file WHERE r.preserve_override=0 AND r.manual=0 AND r.status<>'preserved' AND (r.status='empty' OR r.text=r.source)"
            rows = [
                dict(r)
                for r in db.execute(
                    query + (" AND f.project=?" if project else ""),
                    [project] if project else [],
                )
            ]
        for r in rows:
            locator = json.loads(r["locator"])
            kind = (
                "symbols"
                if symbolic(r["source"])
                else "foreign" if locator.get("preserve_original") is True else ""
            )
            if kind:
                reason = (
                    "Символы, пауза или служебные вставки: оригинал сохранён без перевода"
                    if kind == "symbols"
                    else locator.get("preserve_reason")
                    or "Авторская пометка: оригинал сохранён"
                    + (
                        " (язык: " + str(locator["original_language"]) + ")"
                        if locator.get("original_language")
                        else ""
                    )
                )
                try:
                    self.preserve(r["id"], r["revision"], kind, reason)
                except ValueError:
                    pass

    def mark(self, rid, revision, kind, origin="human"):
        if origin not in {"human", "language"}:
            raise ValueError("Некорректный источник пометки")
        if kind not in {"", "bad", "review"}:
            raise ValueError("Некорректная пометка")
        with self.database.connect() as db:
            r = db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
            previous = db.execute(
                "SELECT * FROM record_marks WHERE record=?", (rid,)
            ).fetchone()
            if not r or r["revision"] != revision:
                raise ValueError("Строка изменилась; обновите её перед пометкой")
            db.execute(
                "INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)",
                (rid, revision, r["text"], r["status"], r["manual"], r["reviewer"]),
            )
            if kind:
                db.execute(
                    "INSERT OR REPLACE INTO record_marks(record,kind,at,was_manual,protected_revision,origin) VALUES (?,?,?,?,?,?)",
                    (rid, kind, time.time(), r["manual"], revision + 1, origin),
                )
            else:
                db.execute("DELETE FROM record_marks WHERE record=?", (rid,))
            db.execute("UPDATE records SET revision=revision+1 WHERE id=?", (rid,))
        return self.record(rid)

    def update(self, rid, revision, text, status, reviewer, reason="", manual=False):
        r = self.record(rid)
        if status not in {"translated", "edited", "verified"} and not (
            status == "preserved" and manual and r["preserve_kind"]
        ):
            raise ValueError("Некорректный этап")
        if r["preserve_kind"]:
            if not manual:
                raise ValueError("Оригинал оставлен без перевода")
            status = "preserved"
        validate(r["source"], text)
        with self.database.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)",
                (rid, revision, r["text"], r["status"], r["manual"], r["reviewer"]),
            )
            cursor = db.execute(
                "UPDATE records SET text=?,status=?,reviewer=?,revision=revision+1,manual=? WHERE id=? AND revision=?",
                (text, status, reviewer, int(manual or r["manual"]), rid, revision),
            )
            if not cursor.rowcount:
                raise ValueError("Строка уже изменена. Обновите её перед сохранением")
            db.execute(
                "INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)",
                (rid, revision + 1, text, status, reviewer, reason, now()),
            )
            if status == "verified" and manual and reviewer.startswith("human"):
                mark = db.execute(
                    "SELECT * FROM record_marks WHERE record=?", (rid,)
                ).fetchone()
                db.execute(
                    "INSERT INTO verification_snapshots VALUES (?,?,?,?)",
                    (
                        rid,
                        revision + 1,
                        r["status"],
                        json.dumps(dict(mark) if mark else {}),
                    ),
                )
                db.execute("DELETE FROM record_marks WHERE record=?", (rid,))
            elif (
                manual
                and r["status"] == "verified"
                and r["reviewer"].startswith("human")
            ):
                snapshot = db.execute(
                    "SELECT * FROM verification_snapshots WHERE record=? ORDER BY revision DESC LIMIT 1",
                    (rid,),
                ).fetchone()
                if snapshot:
                    self._restore_verification_mark(db, rid, revision + 1, snapshot)
            db.execute("UPDATE errors SET resolved=1 WHERE record=?", (rid,))
        return self.record(rid)

    def unverify(self, rid, revision):
        with self.database.connect() as db:
            r = db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
            if not r or r["revision"] != revision:
                raise ValueError(
                    "Строка изменилась; обновите её перед отменой проверки"
                )
            if r["status"] != "verified":
                raise ValueError("Строка не отмечена как проверенная")
            snapshot = db.execute(
                "SELECT * FROM verification_snapshots WHERE record=? AND revision<=? ORDER BY revision DESC LIMIT 1",
                (rid, revision),
            ).fetchone()
            previous = db.execute(
                "SELECT status FROM revision_snapshots WHERE record=? AND revision=?",
                (rid, revision - 1),
            ).fetchone()
            status = (
                snapshot["before_status"]
                if snapshot
                else previous[0] if previous else "translated"
            )
            if status not in {"translated", "edited"}:
                status = "translated"
            db.execute(
                "INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)",
                (rid, revision, r["text"], r["status"], r["manual"], r["reviewer"]),
            )
            db.execute(
                "UPDATE records SET status=?,reviewer='human',revision=revision+1 WHERE id=? AND revision=?",
                (status, rid, revision),
            )
            if snapshot:
                self._restore_verification_mark(db, rid, revision + 1, snapshot)
            db.execute(
                "INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)",
                (
                    rid,
                    revision + 1,
                    r["text"],
                    status,
                    "human",
                    "Отмена ручной проверки; текст сохранён",
                    now(),
                ),
            )
        return self.record(rid)

    def _restore_verification_mark(self, db, rid, revision, snapshot):
        mark = json.loads(snapshot["mark_json"])
        if (
            mark
            and not db.execute(
                "SELECT 1 FROM record_marks WHERE record=?", (rid,)
            ).fetchone()
        ):
            db.execute(
                "INSERT INTO record_marks(record,kind,at,was_manual,protected_revision,origin,independent) VALUES (?,?,?,?,?,?,?)",
                (
                    rid,
                    mark["kind"],
                    mark["at"],
                    mark["was_manual"],
                    revision,
                    mark["origin"],
                    mark.get("independent", 1),
                ),
            )

    def undo(self, rid, revision):
        with self.database.connect() as db:
            r = db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
            previous = db.execute(
                "SELECT * FROM revision_snapshots WHERE record=? AND revision=?",
                (rid, revision - 1),
            ).fetchone()
            last_edit = db.execute(
                "SELECT * FROM history WHERE record=? AND reviewer LIKE 'human%' ORDER BY id DESC LIMIT 1",
                (rid,),
            ).fetchone()
            if (
                r
                and last_edit
                and r["revision"] > last_edit["revision"]
                and r["text"] == last_edit["text"]
            ):
                previous = db.execute(
                    "SELECT * FROM revision_snapshots WHERE record=? AND revision=?",
                    (rid, last_edit["revision"] - 1),
                ).fetchone()
            if not r or r["revision"] != revision:
                raise ValueError("Строка изменилась; обновите её перед отменой")
            if not previous or not r["reviewer"].startswith("human"):
                raise ValueError("Нет сохранённой ручной правки для отмены")
            db.execute(
                "UPDATE records SET text=?,status=?,manual=?,reviewer=?,revision=revision+1 WHERE id=? AND revision=?",
                (
                    previous["text"],
                    previous["status"],
                    previous["manual"],
                    previous["reviewer"],
                    rid,
                    revision,
                ),
            )
            snapshot = db.execute(
                "SELECT * FROM verification_snapshots WHERE record=? ORDER BY revision DESC LIMIT 1",
                (rid,),
            ).fetchone()
            if previous["status"] == "verified" and previous["reviewer"].startswith(
                "human"
            ):
                db.execute(
                    "INSERT INTO verification_snapshots VALUES (?,?,?,?)",
                    (
                        rid,
                        revision + 1,
                        snapshot["before_status"] if snapshot else "translated",
                        snapshot["mark_json"] if snapshot else "{}",
                    ),
                )
                db.execute("DELETE FROM record_marks WHERE record=?", (rid,))
            elif (
                r["status"] == "verified"
                and r["reviewer"].startswith("human")
                and snapshot
            ):
                self._restore_verification_mark(db, rid, revision + 1, snapshot)
            db.execute(
                "INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)",
                (
                    rid,
                    revision + 1,
                    previous["text"],
                    previous["status"],
                    "human",
                    "Отмена сохранённой правки",
                    now(),
                ),
            )
        return self.record(rid)

    def clear_marks(self, project, kind):
        if kind not in {"bad", "review"}:
            raise ValueError("Некорректная папка пометок")
        with self.database.connect() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT m.record FROM record_marks m JOIN records r ON r.id=m.record JOIN files f ON f.id=r.file WHERE f.project=? AND m.kind=?",
                    (project, kind),
                )
            ]
            db.executemany(
                "DELETE FROM record_marks WHERE record=?", [(rid,) for rid in ids]
            )
            db.executemany(
                "UPDATE records SET revision=revision+1 WHERE id=?",
                [(rid,) for rid in ids],
            )
        return {"cleared": len(ids)}
