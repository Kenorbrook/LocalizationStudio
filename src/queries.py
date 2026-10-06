"""Read models for the local UI and diagnostics."""

import json, re
from pathlib import Path
from core import dump


def record_context(store, rid, radius=10):
    from speaker_context import context_entry

    r = store.record(rid)
    radius = max(1, min(30, int(radius)))
    with store.db() as db:
        rows = [
            dict(x)
            for x in db.execute(
                "SELECT r.*,f.path,f.kind FROM records r JOIN files f ON f.id=r.file WHERE r.file=? AND r.scene=? AND r.position BETWEEN ? AND ? ORDER BY r.position",
                (r["file"], r["scene"], r["position"] - radius, r["position"] + radius),
            )
        ]
        before_count = db.execute(
            "SELECT count(*) FROM records WHERE file=? AND scene=? AND position<?",
            (r["file"], r["scene"], r["position"]),
        ).fetchone()[0]
        after_count = db.execute(
            "SELECT count(*) FROM records WHERE file=? AND scene=? AND position>?",
            (r["file"], r["scene"], r["position"]),
        ).fetchone()[0]
    locator = json.loads(r["locator"])
    location = {
        "file": r["path"],
        "scene": r["scene"],
        "speaker": r["speaker"],
        "line": locator.get("line", -1) + 1 if "line" in locator else None,
        "json_path": locator.get("path"),
        "row": locator.get("row", -1) + 1 if "row" in locator else None,
    }
    # A Ren'Py translation block often points to the actual game script.
    if "line" in locator:
        with store.db() as db:
            f = db.execute(
                "SELECT original FROM files WHERE id=?", (r["file"],)
            ).fetchone()
        previous = f[0].splitlines()[: locator["line"] + 1]
        for line in reversed(previous):
            match = re.match(r"\s*# (game/.+\.rpy):(\d+)", line)
            if match:
                location["source_file"] = match[1]
                location["source_line"] = int(match[2])
                break
            if line.strip().startswith("translate "):
                break
    return {
        "target": rid,
        "location": location,
        "rows": rows,
        "metadata": context_entry(store, rid),
        "before_count": before_count,
        "after_count": after_count,
        "order": "file_position",
    }


def job_log(store, jid):
    with store.db() as db:
        job = db.execute(
            "SELECT j.id,p.root FROM jobs j JOIN projects p ON p.id=j.project WHERE j.id=?",
            (jid,),
        ).fetchone()
    if not job:
        raise ValueError("Задача не найдена")
    path = Path(job["root"]) / "translation_tools" / "studio" / f"job-{jid}.log"
    if not path.exists():
        path = store.path.parent / "worker-logs" / f"job-{jid}-process.log"
    if not path.exists():
        return {"text": ""}
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        start = max(0, size - 32768)
        stream.seek(start)
        if start:
            stream.readline()
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    return {"text": "\n".join(lines[-80:])}


def state(store, project, editing=False):
    if project:
        from job_chain import schedule

        schedule(store, project)
    store.archive_completed(project if editing else 0)
    with store.db() as db:
        projects = [
            dict(r)
            for r in db.execute(
                "SELECT p.*,EXISTS(SELECT 1 FROM jobs j WHERE j.project=p.id AND j.state IN ('running','queued')) active FROM projects p WHERE p.hidden=0 AND p.deleting=0 ORDER BY p.id"
            )
        ]
        if project not in {p["id"] for p in projects}:
            project = projects[0]["id"] if projects else 0
        if not project:
            return {
                "projects": projects,
                "files": [],
                "counts": {},
                "jobs": [],
                "project": None,
            }
        files = [
            dict(r)
            for r in db.execute(
                "SELECT f.id,f.path,f.kind,count(r.id) total,sum(r.status='translated') translated,sum(r.status='edited') edited,sum(r.status='verified') verified FROM files f LEFT JOIN records r ON r.file=f.id WHERE f.project=? GROUP BY f.id ORDER BY f.path",
                (project,),
            )
        ]
        counts = dict(
            db.execute(
                "SELECT r.status,count(*) FROM records r JOIN files f ON f.id=r.file WHERE f.project=? GROUP BY r.status",
                (project,),
            ).fetchall()
        )
        jobs = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM jobs WHERE project=? ORDER BY CASE WHEN state IN ('running','queued','paused','waiting','held') THEN 0 ELSE 1 END,CASE WHEN state IN ('running','queued','paused','waiting','held') THEN id ELSE -id END LIMIT 100",
                (project,),
            )
        ]
        errors = db.execute(
            "SELECT count(*) FROM errors WHERE project=? AND resolved=0", (project,)
        ).fetchone()[0]
        pending = db.execute(
            "SELECT count(*) FROM queue q JOIN jobs j ON j.id=q.job WHERE j.project=? AND j.state IN ('queued','running','paused','waiting','held') AND q.state='pending' AND NOT EXISTS(SELECT 1 FROM preserved_records p WHERE p.record=q.record)",
            (project,),
        ).fetchone()[0]
        flags = dict(
            db.execute(
                "SELECT m.kind,count(*) FROM record_marks m JOIN records r ON r.id=m.record JOIN files f ON f.id=r.file WHERE f.project=? GROUP BY m.kind",
                (project,),
            ).fetchall()
        )
    return {
        "projects": projects,
        "project": project,
        "files": files,
        "counts": counts,
        "jobs": jobs,
        "errors": errors,
        "pending": pending,
        "flags": flags,
    }


def queue_page(store, project):
    from process_view import queue_page as upcoming

    return upcoming(store, project)


def records(store, file, offset=0, search="", status=""):
    q = "FROM records r LEFT JOIN record_marks m ON m.record=r.id WHERE r.file=?"
    args = [file]
    if search:
        q += " AND (source LIKE ? OR text LIKE ?)"
        args += ["%" + search + "%"] * 2
    if status:
        q += " AND status=?"
        args.append(status)
    with store.db() as db:
        total = db.execute("SELECT count(*) " + q, args).fetchone()[0]
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT r.*,coalesce(m.kind,'') flag,m.at flagged_at,m.was_manual flag_previous_manual,coalesce(json_extract((SELECT result FROM language_checks WHERE record=r.id),'$.reason'),'') language_note,coalesce((SELECT reason FROM preserved_records WHERE record=r.id),'') preserve_reason,coalesce((SELECT kind FROM preserved_records WHERE record=r.id),'') preserve_kind "
                + q
                + " ORDER BY position LIMIT 50 OFFSET ?",
                args + [max(0, int(offset))],
            )
        ]
    return {"rows": rows, "total": total, "offset": offset}
