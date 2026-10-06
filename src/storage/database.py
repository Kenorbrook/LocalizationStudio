"""Connection lifetime and persistent ID allocation."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from schema import initialize


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            initialize(db)

    @contextmanager
    def connect(self):
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

    @staticmethod
    def next_id(db, table):
        if table not in {"projects", "jobs", "files", "records"}:
            raise ValueError("Некорректный счётчик")
        return db.execute(
            f"UPDATE id_counters SET next_id=max(next_id,(SELECT coalesce(max(id),0)+1 FROM {table}))+1 WHERE name=? RETURNING next_id-1",
            (table,),
        ).fetchone()[0]
