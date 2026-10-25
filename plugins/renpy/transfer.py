"""Carry only unambiguous translations from the same game's source project."""

import re
from collections import defaultdict

from validation import validate


def scene(value):
    match = re.match(r"translate\s+\w+\s+(.+?):?$", value)
    return match[1].rstrip(":") if match else value


def carry(store, source, destination):
    with store.db() as db:
        old = [
            dict(r)
            for r in db.execute(
                "SELECT r.*,p.kind preserve_kind,p.reason preserve_reason,m.kind mark_kind FROM records r JOIN files f ON f.id=r.file LEFT JOIN preserved_records p ON p.record=r.id LEFT JOIN record_marks m ON m.record=r.id WHERE f.project=? AND r.status<>'empty' AND r.text<>''",
                (source,),
            )
        ]
        new = [
            dict(r)
            for r in db.execute(
                "SELECT r.* FROM records r JOIN files f ON f.id=r.file WHERE f.project=?",
                (destination,),
            )
        ]
    choices = defaultdict(list)
    for record in old:
        choices[(record["source"], record["speaker"], scene(record["scene"]))].append(
            record
        )
    transferred = ambiguous = 0
    for record in new:
        values = choices.get(
            (record["source"], record["speaker"], scene(record["scene"])), []
        )
        if not values or record["status"] != "empty":
            continue
        if (
            any(r["mark_kind"] for r in values)
            or len({(r["text"], r["preserve_kind"] or "") for r in values}) != 1
        ):
            ambiguous += 1
            store.mark(record["id"], record["revision"], "review", origin="engine")
            continue
        best = max(
            values,
            key=lambda r: (r["manual"], r["status"] == "verified", r["revision"]),
        )
        try:
            validate(record["source"], best["text"])
            if best["preserve_kind"]:
                store.preserve(
                    record["id"],
                    record["revision"],
                    best["preserve_kind"],
                    best["preserve_reason"] or "Из исходного проекта",
                    confirmed=True,
                )
            else:
                store.update(
                    record["id"],
                    record["revision"],
                    best["text"],
                    best["status"],
                    "studio:renpy-carry",
                    "Перенос из исходного проекта той же игры",
                    manual=bool(best["manual"]),
                )
            transferred += 1
        except ValueError:
            ambiguous += 1
            store.mark(record["id"], record["revision"], "review", origin="engine")
    return dict(transferred=transferred, ambiguous=ambiguous)
