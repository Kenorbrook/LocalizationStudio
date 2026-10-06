"""Exact literals; foreign speech is preserved only by an explicit author/user decision."""

from core import TOKEN


def symbolic(source):
    return bool(source.strip()) and not any(c.isalnum() for c in TOKEN.sub("", source))


def folder(store, project, offset=0):
    from process_view import FIELDS

    query = "FROM records r JOIN files f ON f.id=r.file LEFT JOIN record_marks m ON m.record=r.id WHERE f.project=? AND r.status='preserved'"
    with store.db() as db:
        total = db.execute("SELECT count(*) " + query, (project,)).fetchone()[0]
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT "
                + FIELDS
                + " "
                + query
                + " ORDER BY coalesce((SELECT at FROM preserved_records WHERE record=r.id),0) DESC,r.id DESC LIMIT 50 OFFSET ?",
                (project, max(0, int(offset))),
            )
        ]
    return {
        "rows": rows,
        "total": total,
        "offset": max(0, int(offset)),
        "page_size": 50,
    }
