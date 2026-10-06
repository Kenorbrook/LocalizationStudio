"""Persistent, versioned localization workspace. Only stdlib dependencies."""

from __future__ import annotations
import collections, hashlib, json, re, shutil, sqlite3, time
from contextlib import contextmanager
from pathlib import Path

from paths import ROOT, APP_HOME, UI_ROOT, POLICY_PATH, SHARED
from local_editor import refusal_error

from serialization import now, dump
from schema import initialize
from extraction import extract, literal

TOKEN = re.compile(
    r"\[[^\[\]\n]+\]|\{[^{}\n]+\}|%\([^)]+\)[#0+\-\d.]*[sdif]|%(?:[#0+\-\d.]*[sdif])(?=\W|$)|\\[nrt]"
)


def validate(source, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Пустой перевод")
    if collections.Counter(TOKEN.findall(source)) != collections.Counter(
        TOKEN.findall(text)
    ):
        raise ValueError("Не совпадают теги или переменные")
    if any(source.count(c) != text.count(c) for c in "\n\r\t"):
        raise ValueError("Не совпадают переносы строк или табуляция")
    error = refusal_error(source, text)
    if error:
        raise ValueError(error)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            initialize(db)
        with self.db() as db:
            needs_preservation = not db.execute(
                "SELECT 1 FROM schema_versions WHERE name='preservation-v1'"
            ).fetchone()
        if needs_preservation:
            self.classify_preserved()
            with self.db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO schema_versions VALUES ('preservation-v1')"
                )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def project(self, root):
        p = Path(root).resolve()
        if not p.is_dir():
            raise ValueError("Папка проекта не найдена")
        with self.db() as db:
            existing = db.execute(
                "SELECT * FROM projects WHERE root=? COLLATE NOCASE", (str(p),)
            ).fetchone()
            if existing:
                if existing["deleting"]:
                    raise ValueError("Сначала завершите удаление проекта")
                db.execute(
                    "UPDATE projects SET hidden=0,completed=0,keep_visible=1 WHERE id=?",
                    (existing["id"],),
                )
                return dict(
                    db.execute(
                        "SELECT * FROM projects WHERE id=?", (existing["id"],)
                    ).fetchone()
                )
            pid = self.next_id(db, "projects")
            db.execute(
                "INSERT OR IGNORE INTO projects(id,name,root) VALUES (?,?,?)",
                (pid, p.name, str(p)),
            )
            return dict(
                db.execute("SELECT * FROM projects WHERE root=?", (str(p),)).fetchone()
            )

    def hide_project(self, pid, hidden=True):
        with self.db() as db:
            if not db.execute(
                "UPDATE projects SET hidden=?,completed=0,keep_visible=1 WHERE id=? AND deleting=0",
                (int(hidden), pid),
            ).rowcount:
                raise ValueError("Проект не найден")
        return {"ok": True}

    @staticmethod
    def next_id(db, table):
        if table not in {"projects", "jobs", "files", "records"}:
            raise ValueError("Некорректный счётчик")
        return db.execute(
            f"UPDATE id_counters SET next_id=max(next_id,(SELECT coalesce(max(id),0)+1 FROM {table}))+1 WHERE name=? RETURNING next_id-1",
            (table,),
        ).fetchone()[0]

    def archive_completed(self, editing_project=0):
        with self.db() as db:
            db.execute(
                "UPDATE projects SET keep_visible=0 WHERE EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id AND r.status='empty')"
            )
            db.execute(
                "UPDATE projects SET hidden=1,completed=1 WHERE id<>? AND hidden=0 AND keep_visible=0 AND deleting=0 AND EXISTS(SELECT 1 FROM jobs completed_job WHERE completed_job.project=projects.id AND completed_job.stage='translate' AND completed_job.state='done') AND EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id) AND NOT EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id AND r.status='empty') AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.project=projects.id AND j.state IN ('queued','running','paused','waiting','held'))",
                (editing_project,),
            )

    def complete_project(self, pid):
        with self.db() as db:
            if not db.execute(
                "SELECT id FROM projects WHERE id=? AND deleting=0", (pid,)
            ).fetchone():
                raise ValueError("Проект не найден")
            total, empty = db.execute(
                "SELECT count(*),coalesce(sum(r.status='empty'),0) FROM records r JOIN files f ON f.id=r.file WHERE f.project=?",
                (pid,),
            ).fetchone()
            if not total or empty:
                raise ValueError("В проекте ещё есть строки без перевода")
            db.execute(
                "UPDATE projects SET hidden=1,completed=1,keep_visible=0 WHERE id=?",
                (pid,),
            )
        return {"ok": True}

    def completed_projects(self):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM projects WHERE completed=1 AND deleting=0 ORDER BY id"
                )
            ]

    def delete_project_data(self, pid):
        with self.db() as db:
            if not db.execute(
                "SELECT id FROM projects WHERE id=? AND deleting=1", (pid,)
            ).fetchone():
                raise ValueError("Проект не подготовлен к удалению")
            record_query = "SELECT r.id FROM records r JOIN files f ON f.id=r.file WHERE f.project=?"
            for table in [
                "history",
                "revision_snapshots",
                "proposals",
                "record_marks",
                "preserved_records",
                "language_checks",
                "fragment_manifests",
            ]:
                db.execute(
                    f"DELETE FROM {table} WHERE record IN ({record_query})", (pid,)
                )
            db.execute(
                "DELETE FROM mcp_requests WHERE job IN (SELECT id FROM jobs WHERE project=?)",
                (pid,),
            )
            db.execute(
                "DELETE FROM queue_overrides WHERE job IN (SELECT id FROM jobs WHERE project=?)",
                (pid,),
            )
            db.execute(
                "DELETE FROM queue WHERE job IN (SELECT id FROM jobs WHERE project=?)",
                (pid,),
            )
            for table in ["fragment_cache", "fragment_plans"]:
                db.execute(
                    f"DELETE FROM {table} WHERE key IN (SELECT key FROM cache_owners WHERE project=? AND NOT EXISTS(SELECT 1 FROM cache_owners other WHERE other.key=cache_owners.key AND other.project<>?))",
                    (pid, pid),
                )
            db.execute("DELETE FROM cache_owners WHERE project=?", (pid,))
            db.execute(f"DELETE FROM records WHERE id IN ({record_query})", (pid,))
            for table in [
                "files",
                "jobs",
                "errors",
                "project_analysis",
                "process_preferences",
            ]:
                db.execute(f"DELETE FROM {table} WHERE project=?", (pid,))
            db.execute("DELETE FROM projects WHERE id=?", (pid,))
        return {"deleted": True}

    def hidden_projects(self):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT p.*,EXISTS(SELECT 1 FROM jobs j WHERE j.project=p.id AND j.state IN ('running','queued')) active FROM projects p WHERE (p.hidden=1 AND p.completed=0) OR p.deleting=1 ORDER BY p.id"
                )
            ]

    def import_files(self, project, paths):
        counts = {"files": 0, "added": 0, "errors": []}
        for entry in paths:
            p = Path(entry).resolve()
            try:
                records, original, kind = extract(p)
                if not records:
                    raise ValueError("Диалоги или строки не найдены")
                digest = hashlib.sha256(original.encode()).hexdigest()
                with self.db() as db:
                    old = db.execute(
                        "SELECT * FROM files WHERE project=? AND path=?",
                        (project, str(p)),
                    ).fetchone()
                    if old and old["hash"] != digest:
                        raise ValueError(
                            "Файл изменился после импорта. Добавьте его копию как новую версию; существующие правки сохранены"
                        )
                    db.execute(
                        "INSERT OR IGNORE INTO files(id,project,path,kind,hash,original) VALUES (?,?,?,?,?,?)",
                        (
                            self.next_id(db, "files"),
                            project,
                            str(p),
                            kind,
                            digest,
                            original,
                        ),
                    )
                    fid = db.execute(
                        "SELECT id FROM files WHERE project=? AND path=?",
                        (project, str(p)),
                    ).fetchone()[0]
                    for i, r in enumerate(records):
                        value = r["text"] if isinstance(r["text"], str) else ""
                        state = (
                            "translated" if value and value != r["source"] else "empty"
                        )
                        counts["added"] += db.execute(
                            "INSERT OR IGNORE INTO records(id,file,position,source,text,speaker,scene,locator,status) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                self.next_id(db, "records"),
                                fid,
                                i,
                                r["source"],
                                value,
                                r["speaker"],
                                r["scene"],
                                dump(r["locator"]),
                                state,
                            ),
                        ).rowcount
                self.classify_preserved(project)
                counts["files"] += 1
            except Exception as e:
                counts["errors"].append({"path": str(p), "message": str(e)})
                with self.db() as db:
                    db.execute(
                        "INSERT INTO errors(project,message,at) VALUES (?,?,?)",
                        (project, str(p) + ": " + str(e), now()),
                    )
        return counts

    def record(self, rid):
        with self.db() as db:
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
        with self.db() as db:
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
        with self.db() as db:
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

        with self.db() as db:
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
        with self.db() as db:
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
        with self.db() as db:
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
            db.execute("UPDATE errors SET resolved=1 WHERE record=?", (rid,))
        return self.record(rid)

    def undo(self, rid, revision):
        with self.db() as db:
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
        with self.db() as db:
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

        self.classify_preserved(project)
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
        with self.db() as db:
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
            jid = self.next_id(db, "jobs")
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
        with self.db() as db:
            p = db.execute("SELECT project FROM jobs WHERE id=?", (job,)).fetchone()[0]
            db.execute(
                "INSERT INTO errors(project,job,record,message,at) VALUES (?,?,?,?,?)",
                (p, job, record, message[:2000], now()),
            )
        if record:
            try:
                from error_workflow import budget

                with self.db() as db:
                    j = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
                    settings = json.loads(j["settings"])
                    override = db.execute(
                        "SELECT settings FROM queue_overrides WHERE job=? AND record=?",
                        (job, record),
                    ).fetchone()
                    if override:
                        settings.update(json.loads(override[0]))
                estimate = budget(self, p, record, j["stage"], settings)
                with self.db() as db:
                    db.execute(
                        "UPDATE errors SET budget_json=? WHERE job=? AND record=? AND resolved=0",
                        (dump(estimate), job, record),
                    )
            except (ValueError, KeyError, TypeError):
                pass

    def export(self, fid, destination, apply=False):
        with self.db() as db:
            f = dict(db.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone())
            records = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM records WHERE file=? ORDER BY position", (fid,)
                )
            ]
        p = Path(f["path"])
        target = Path(destination).resolve()
        if apply and f["kind"] not in {"renpy", "csv", "json", "jsonl"}:
            raise ValueError("Для TXT доступен только экспорт корпуса")
        if apply and (
            target != p.resolve() or f["kind"] == "renpy" and "/tl/" not in p.as_posix()
        ):
            raise ValueError(
                "В игру можно записывать только существующий слой перевода. Исходные Ren’Py скрипты не переписываются"
            )
        if apply:
            if (
                hashlib.sha256(p.read_text(encoding="utf-8-sig").encode()).hexdigest()
                != f["hash"]
            ):
                raise ValueError(
                    "Игровой файл изменён с момента импорта. Запись остановлена"
                )
            raise ValueError(
                "Запись в игру в этой версии отключена: экспортируйте отдельный файл и проверьте его в игре"
            )
        if target == p.resolve():
            raise ValueError("Экспортируйте в другой файл; оригинал защищён")
        if f["kind"] == "renpy" and "/tl/" in p.as_posix():
            lines = f["original"].splitlines(keepends=True)
            for r in records:
                if r["status"] == "empty":
                    continue
                validate(r["source"], r["text"])
                loc = json.loads(r["locator"])
                line = lines[loc["line"]]
                lines[loc["line"]] = (
                    line[: loc["start"]]
                    + json.dumps(r["text"], ensure_ascii=False)
                    + line[loc["end"] :]
                )
            output = "".join(lines)
        else:
            output = dump(
                [
                    {
                        "id": r["id"],
                        "source": r["source"],
                        "translation": r["text"],
                        "status": r["status"],
                        "speaker": r["speaker"],
                        "scene": r["scene"],
                    }
                    for r in records
                ]
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(
                target, target.with_name(target.name + ".backup-" + str(time.time_ns()))
            )
        target.write_text(output, encoding="utf-8")
        return str(target)
