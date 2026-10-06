from __future__ import annotations
from pathlib import Path


class ProjectsRepository:
    def __init__(self, database):
        self.database = database

    def project(self, root):
        p = Path(root).resolve()
        if not p.is_dir():
            raise ValueError("Папка проекта не найдена")
        with self.database.connect() as db:
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
            pid = self.database.next_id(db, "projects")
            db.execute(
                "INSERT OR IGNORE INTO projects(id,name,root) VALUES (?,?,?)",
                (pid, p.name, str(p)),
            )
            return dict(
                db.execute("SELECT * FROM projects WHERE root=?", (str(p),)).fetchone()
            )

    def hide_project(self, pid, hidden=True):
        with self.database.connect() as db:
            if not db.execute(
                "UPDATE projects SET hidden=?,completed=0,keep_visible=1 WHERE id=? AND deleting=0",
                (int(hidden), pid),
            ).rowcount:
                raise ValueError("Проект не найден")
        return {"ok": True}

    def archive_completed(self, editing_project=0):
        with self.database.connect() as db:
            db.execute(
                "UPDATE projects SET keep_visible=0 WHERE EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id AND r.status='empty')"
            )
            db.execute(
                "UPDATE projects SET hidden=1,completed=1 WHERE id<>? AND hidden=0 AND keep_visible=0 AND deleting=0 AND EXISTS(SELECT 1 FROM jobs completed_job WHERE completed_job.project=projects.id AND completed_job.stage='translate' AND completed_job.state='done') AND EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id) AND NOT EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id AND r.status='empty') AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.project=projects.id AND j.state IN ('queued','running','paused','waiting','held'))",
                (editing_project,),
            )

    def complete_project(self, pid):
        with self.database.connect() as db:
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
        with self.database.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM projects WHERE completed=1 AND deleting=0 ORDER BY id"
                )
            ]

    def delete_project_data(self, pid):
        with self.database.connect() as db:
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
        with self.database.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT p.*,EXISTS(SELECT 1 FROM jobs j WHERE j.project=p.id AND j.state IN ('running','queued')) active FROM projects p WHERE (p.hidden=1 AND p.completed=0) OR p.deleting=1 ORDER BY p.id"
                )
            ]
