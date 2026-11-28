"""Project throughput from saved translation revisions."""

import re
import time
from datetime import datetime


def overview(store, project, period, now=None):
    now = int(time.time() if now is None else now)
    if period not in {"hour", "day", "session"}:
        raise ValueError("Неизвестный период")
    with store.db() as db:
        if period == "session":
            start = db.execute(
                "SELECT max(run_started) FROM jobs WHERE project=? AND stage='translate'",
                (project,),
            ).fetchone()[0]
            start = min(now, int(start or now))
        else:
            start = now - (3600 if period == "hour" else 86400)
        size = {"hour": 180, "day": 3600}.get(period)
        if size is None:
            size = max(60, ((now - start + 24) // 24 + 59) // 60 * 60)
        bins = [
            {"at": t, "lines": 0, "words": 0, "models": {}}
            for t in range(start, max(now, start + 1), size)
        ]
        models = {}
        cutoff = datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S")
        for row in db.execute(
            "SELECT h.at,h.text,h.reviewer FROM files f JOIN records r ON r.file=f.id "
            "JOIN history h ON h.record=r.id WHERE f.project=? AND h.status='translated' "
            "AND h.at>=? AND (h.reviewer LIKE 'local:%' OR h.reviewer LIKE 'cloud:%' OR h.reviewer LIKE 'mcp:%')",
            (project, cutoff),
        ):
            try:
                stamp = int(
                    datetime.strptime(row["at"], "%Y-%m-%d %H:%M:%S").timestamp()
                )
            except ValueError:
                continue
            if not start <= stamp <= now:
                continue
            words = len(re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*", row["text"]))
            name = row["reviewer"]
            bucket = bins[min((stamp - start) // size, len(bins) - 1)]
            model = models.setdefault(name, {"lines": 0, "words": 0})
            contribution = bucket["models"].setdefault(name, {"lines": 0, "words": 0})
            for event in (bucket, model, contribution):
                event["lines"] += 1
                event["words"] += words
    return {"buckets": bins, "models": models, "bucket_seconds": size, "end": now}
