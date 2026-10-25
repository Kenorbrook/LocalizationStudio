"""Read-only readiness for the local game-localization journey."""

from pathlib import Path

from game_detection import saved_report


def overview(store, project):
    with store.db() as db:
        row = db.execute(
            "SELECT id,root,settings FROM projects WHERE id=? AND deleting=0",
            (project,),
        ).fetchone()
        if not row:
            raise ValueError("Проект не найден")
        files = [
            dict(f)
            for f in db.execute(
                "SELECT id,path,kind FROM files WHERE project=? ORDER BY path",
                (project,),
            )
        ]
        counts = dict(
            db.execute(
                "SELECT r.status,count(*) FROM records r JOIN files f ON f.id=r.file WHERE f.project=? GROUP BY r.status",
                (project,),
            ).fetchall()
        )
        errors = db.execute(
            "SELECT count(*) FROM errors WHERE project=? AND resolved=0", (project,)
        ).fetchone()[0]
        review = db.execute(
            "SELECT count(*) FROM record_marks m JOIN records r ON r.id=m.record JOIN files f ON f.id=r.file WHERE f.project=? AND m.kind='review'",
            (project,),
        ).fetchone()[0]
        jobs = [
            dict(j)
            for j in db.execute(
                "SELECT id,stage,provider,state,worker_active FROM jobs WHERE project=? AND (state IN ('running','queued','waiting','held','paused') OR worker_active=1) ORDER BY id",
                (project,),
            )
        ]
    report = saved_report(store, project)
    candidates = report.get("candidates", []) if report else []
    supported = [c for c in candidates if c.get("supported")]
    imported = {str(Path(f["path"])) for f in files}
    pending_files = [
        c["path"]
        for c in supported
        if str(Path(row["root"]) / c["path"]) not in imported
    ]
    total = sum(counts.values())
    preserved = counts.get("preserved", 0)
    edited = counts.get("edited", 0) + counts.get("verified", 0)
    return {
        "project": project,
        "engine": report.get("engine") if report else None,
        "scan_available": bool(report),
        "scan_partial": bool(report and report.get("partial")),
        "archives": len(report.get("archives", [])) if report else 0,
        "pending_files": pending_files,
        "total": total,
        "empty": counts.get("empty", 0),
        "translated": max(0, total - counts.get("empty", 0) - preserved),
        "edited": edited,
        "preserved": preserved,
        "errors": errors,
        "manual_review": review,
        "jobs": jobs,
        "installation": {
            "available": False,
            "reason": "Автоматическая установка перевода в игру ещё не реализована. Экспорт сохраняет отдельный файл и не устанавливает перевод.",
        },
        "files": [
            {
                **f,
                "export_kind": (
                    "rpy"
                    if f["kind"] == "renpy" and "/tl/" in f["path"].replace("\\", "/")
                    else "json"
                ),
            }
            for f in files
        ],
        # Counts describe imported records only, never all text hidden in a game.
        "coverage_confirmed": False,
    }
