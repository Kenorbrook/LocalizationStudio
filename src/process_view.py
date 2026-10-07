"""Bounded upcoming rows and durable recent results; marks are project folders."""

import math, time
from core import dump

DEFAULTS = {"max_phrases": 200, "max_seconds": 600, "page_size": 50}


def preferences(store, pid):
    with store.db() as db:
        row = db.execute(
            "SELECT max_phrases,max_seconds,page_size FROM process_preferences WHERE project=?",
            (pid,),
        ).fetchone()
    return dict(row) if row else dict(DEFAULTS)


def save_preferences(store, pid, values):
    result = {}
    for key, maximum in [
        ("max_phrases", 1000000),
        ("max_seconds", 31536000),
        ("page_size", 200),
    ]:
        value = float(values.get(key, DEFAULTS[key]))
        if (
            not math.isfinite(value)
            or not 0 <= value <= maximum
            or (key != "max_seconds" and value != int(value))
            or (key == "page_size" and value < 10)
        ):
            raise ValueError("Некорректное ограничение: " + key)
        result[key] = value if key == "max_seconds" else int(value)
    with store.db() as db:
        if not db.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone():
            raise ValueError("Проект не найден")
        db.execute(
            "INSERT OR REPLACE INTO process_preferences VALUES (?,?,?,?)",
            (pid, result["max_phrases"], result["max_seconds"], result["page_size"]),
        )
    return result


FIELDS = "r.*,f.path,coalesce(m.kind,'') flag,m.at flagged_at,m.was_manual flag_previous_manual,coalesce(json_extract((SELECT result FROM language_checks WHERE record=r.id),'$.reason'),'') language_note,coalesce((SELECT reason FROM preserved_records WHERE record=r.id),'') preserve_reason,coalesce((SELECT kind FROM preserved_records WHERE record=r.id),'') preserve_kind"


def queue_page(store, pid):
    pref = preferences(store, pid)
    with store.db() as db:
        job = db.execute(
            "SELECT * FROM jobs WHERE project=? AND state IN ('queued','running','paused') ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        if not job:
            return {
                "rows": [],
                "total": 0,
                "offset": 0,
                "block": None,
                "page_size": pref["page_size"],
            }
        rows = [
            dict(r)
            for r in db.execute(
                f"SELECT {FIELDS},q.state queue_state FROM queue q JOIN records r ON r.id=q.record JOIN files f ON f.id=r.file LEFT JOIN record_marks m ON m.record=r.id WHERE q.job=? AND q.state='pending' AND r.status<>'preserved' ORDER BY q.priority,q.record LIMIT ?",
                (job["id"], pref["page_size"]),
            )
        ]
        total = db.execute(
            "SELECT count(*) FROM queue WHERE job=? AND state='pending'", (job["id"],)
        ).fetchone()[0]
    return {
        "rows": rows,
        "total": total,
        "offset": rows[0]["position"] if rows else 0,
        "block": f"{job['id']}:{rows[0]['id']}" if rows else None,
        "page_size": pref["page_size"],
    }


def history_page(store, pid, offset=0):
    pref = preferences(store, pid)
    offset = max(0, int(offset))
    cte = "WITH recent AS (SELECT q.*,row_number() OVER (PARTITION BY q.record ORDER BY q.finished_at DESC,q.job DESC) ranking FROM queue q JOIN jobs j ON j.id=q.job WHERE j.project=? AND q.state='done') "
    query = "FROM recent q JOIN records r ON r.id=q.record JOIN files f ON f.id=r.file LEFT JOIN record_marks m ON m.record=r.id WHERE q.ranking=1 AND r.status<>'preserved'"
    args = [pid]
    if pref["max_seconds"]:
        query += " AND q.finished_at>=?"
        args.append(time.time() - pref["max_seconds"])
    with store.db() as db:
        available = db.execute(cte + "SELECT count(*) " + query, args).fetchone()[0]
        total = (
            min(available, pref["max_phrases"]) if pref["max_phrases"] else available
        )
        limit = min(pref["page_size"], max(0, total - offset))
        rows = [
            dict(r)
            for r in db.execute(
                cte
                + f"SELECT {FIELDS},q.job activity_job,q.finished_at processed_at,q.state queue_state "
                + query
                + " ORDER BY q.finished_at DESC,q.job DESC,q.record DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            )
        ]
    # A record may appear at different stages; render the most recent version,
    # but retain distinct completion events for accurate history retention.
    for row in rows:
        row["activity_key"] = f"{row['activity_job']}:{row['id']}"
    return {
        "rows": rows,
        "total": total,
        "offset": offset,
        "page_size": pref["page_size"],
        "preferences": pref,
    }


def marks_page(store, pid, kind, offset=0, retained=()):
    if kind not in {"bad", "review"}:
        raise ValueError("Некорректная папка пометок")
    retained = list(dict.fromkeys(int(rid) for rid in retained))
    if len(retained) > 1000:
        raise ValueError("Слишком много сохранённых строк на экране")
    membership = "m.kind=?"
    args = [pid, kind]
    if retained:
        membership += (
            " OR (m.record IS NULL AND r.status='verified' AND r.reviewer LIKE 'human%' AND r.id IN ("
            + ",".join("?" for _ in retained)
            + ") AND json_extract((SELECT mark_json FROM verification_snapshots v WHERE v.record=r.id ORDER BY v.revision DESC LIMIT 1),'$.kind')=?)"
        )
        args.extend(retained + [kind])
    query = (
        "FROM records r JOIN files f ON f.id=r.file LEFT JOIN record_marks m ON m.record=r.id WHERE f.project=? AND ("
        + membership
        + ")"
    )
    offset = max(0, int(offset))
    with store.db() as db:
        total = db.execute("SELECT count(*) " + query, args).fetchone()[0]
        rows = [
            dict(r)
            for r in db.execute(
                f"SELECT {FIELDS} "
                + query
                + " ORDER BY coalesce(m.at,(SELECT json_extract(mark_json,'$.at') FROM verification_snapshots v WHERE v.record=r.id ORDER BY v.revision DESC LIMIT 1),0) DESC,r.id DESC LIMIT 50 OFFSET ?",
                args + [offset],
            )
        ]
    return {"rows": rows, "total": total, "offset": offset, "page_size": 50}
