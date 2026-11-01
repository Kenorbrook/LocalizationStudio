"""Inspect the exact context builder without migrations, inference or project writes."""

import argparse
import json
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from plugin_backend import seed_module


class ReadOnlyProject:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def record(self, rid):
        with self.db() as db:
            row = db.execute(
                "SELECT r.*,f.project,f.path FROM records r JOIN files f ON f.id=r.file WHERE r.id=?",
                (rid,),
            ).fetchone()
            if row is None:
                raise ValueError("Строка не найдена")
            return dict(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data/studio.sqlite3"))
    parser.add_argument("--record", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    module = seed_module("hermes", "renpy_context")
    entry = module.context_entry(ReadOnlyProject(args.db), args.record)
    if args.output:
        args.output.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "id": args.record,
                "summary": entry.get("context_summary"),
                "before_ids": [r["id"] for r in entry["context_before"]],
                "after_ids": [r["id"] for r in entry["context_after"]],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
