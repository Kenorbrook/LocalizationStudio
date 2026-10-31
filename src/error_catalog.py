"""Current error cards are unique records; immutable attempts remain in history."""

from script_validation import MIXED_SCRIPT_PREFIX

LATEST = """e.project=? AND e.resolved=0 AND (
    e.record IS NULL OR e.record=0 OR e.id=(
        SELECT max(newer.id) FROM errors newer
        WHERE newer.project=e.project AND newer.record=e.record AND newer.resolved=0
    ))"""


def count(db, project):
    return db.execute(
        "SELECT count(*) FROM errors e WHERE " + LATEST, (project,)
    ).fetchone()[0]


def repair_candidates(db, project, record=None):
    query = (
        "SELECT e.*,j.settings retry_settings,j.provider retry_provider,j.stage "
        "FROM errors e JOIN jobs j ON j.id=e.job JOIN records r ON r.id=e.record "
        "JOIN files f ON f.id=r.file WHERE "
        + LATEST
        + " AND f.project=e.project AND j.provider='local' AND j.stage='translate' "
        "AND r.status='empty' AND r.manual=0 AND e.message LIKE ?"
    )
    args = [project, MIXED_SCRIPT_PREFIX + "%"]
    if record is not None:
        query += " AND e.record=?"
        args.append(int(record))
    return [dict(r) for r in db.execute(query + " ORDER BY e.id DESC", args)]


def page(store, project, offset=0):
    with store.db() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT e.*,r.source,r.text,r.revision,r.manual,r.position,r.status,"
                "f.path file_path,j.stage,j.settings retry_settings,j.provider retry_provider "
                "FROM errors e LEFT JOIN records r ON r.id=e.record "
                "LEFT JOIN files f ON f.id=r.file LEFT JOIN jobs j ON j.id=e.job WHERE "
                + LATEST
                + " ORDER BY e.id DESC LIMIT 100 OFFSET ?",
                (project, max(0, offset)),
            )
        ]
        for row in rows:
            row["attempts"] = (
                [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,job,message,at FROM errors WHERE project=? AND resolved=0 "
                        "AND record=? ORDER BY id DESC",
                        (project, row["record"]),
                    )
                ]
                if row["record"]
                else [
                    dict(
                        id=row["id"],
                        job=row["job"],
                        message=row["message"],
                        at=row["at"],
                    )
                ]
            )
            row["attempt_count"] = len(row["attempts"])
            row["repairable"] = bool(
                row["record"]
                and row["retry_provider"] == "local"
                and row["stage"] == "translate"
                and row["status"] == "empty"
                and not row["manual"]
                and row["message"].startswith(MIXED_SCRIPT_PREFIX)
            )
    from error_workflow import fill_budgets

    return fill_budgets(store, rows)
