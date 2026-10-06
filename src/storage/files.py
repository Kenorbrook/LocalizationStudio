from __future__ import annotations
import hashlib, json, shutil, time
from pathlib import Path
from serialization import now, dump
from validation import validate
from extraction import extract


class FilesRepository:
    def __init__(self, database, records):
        self.database = database
        self.records = records

    def import_files(self, project, paths):
        counts = {"files": 0, "added": 0, "errors": []}
        for entry in paths:
            p = Path(entry).resolve()
            try:
                records, original, kind = extract(p)
                if not records:
                    raise ValueError("Диалоги или строки не найдены")
                digest = hashlib.sha256(original.encode()).hexdigest()
                with self.database.connect() as db:
                    old = db.execute(
                        "SELECT * FROM files WHERE project=? AND path=?",
                        (project, str(p)),
                    ).fetchone()
                    if old and old["hash"] != digest:
                        raise ValueError(
                            "Файл изменился после импорта. Добавьте его копию как новую версию; существующие правки сохранены"
                        )
                    db.execute(
                        "INSERT OR IGNORE INTO files(id,project,path,kind,hash,original) VALUES (?,?,?,?,?,?)",
                        (
                            self.database.next_id(db, "files"),
                            project,
                            str(p),
                            kind,
                            digest,
                            original,
                        ),
                    )
                    fid = db.execute(
                        "SELECT id FROM files WHERE project=? AND path=?",
                        (project, str(p)),
                    ).fetchone()[0]
                    for i, r in enumerate(records):
                        value = r["text"] if isinstance(r["text"], str) else ""
                        state = (
                            "translated" if value and value != r["source"] else "empty"
                        )
                        counts["added"] += db.execute(
                            "INSERT OR IGNORE INTO records(id,file,position,source,text,speaker,scene,locator,status) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                self.database.next_id(db, "records"),
                                fid,
                                i,
                                r["source"],
                                value,
                                r["speaker"],
                                r["scene"],
                                dump(r["locator"]),
                                state,
                            ),
                        ).rowcount
                self.records.classify_preserved(project)
                counts["files"] += 1
            except Exception as e:
                counts["errors"].append({"path": str(p), "message": str(e)})
                with self.database.connect() as db:
                    db.execute(
                        "INSERT INTO errors(project,message,at) VALUES (?,?,?)",
                        (project, str(p) + ": " + str(e), now()),
                    )
        return counts

    def export(self, fid, destination, apply=False):
        with self.database.connect() as db:
            f = dict(db.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone())
            records = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM records WHERE file=? ORDER BY position", (fid,)
                )
            ]
        p = Path(f["path"])
        target = Path(destination).resolve()
        if apply and f["kind"] not in {"renpy", "csv", "json", "jsonl"}:
            raise ValueError("Для TXT доступен только экспорт корпуса")
        if apply and (
            target != p.resolve() or f["kind"] == "renpy" and "/tl/" not in p.as_posix()
        ):
            raise ValueError(
                "В игру можно записывать только существующий слой перевода. Исходные Ren’Py скрипты не переписываются"
            )
        if apply:
            if (
                hashlib.sha256(p.read_text(encoding="utf-8-sig").encode()).hexdigest()
                != f["hash"]
            ):
                raise ValueError(
                    "Игровой файл изменён с момента импорта. Запись остановлена"
                )
            raise ValueError(
                "Запись в игру в этой версии отключена: экспортируйте отдельный файл и проверьте его в игре"
            )
        if target == p.resolve():
            raise ValueError("Экспортируйте в другой файл; оригинал защищён")
        if f["kind"] == "renpy" and "/tl/" in p.as_posix():
            lines = f["original"].splitlines(keepends=True)
            for r in records:
                if r["status"] == "empty":
                    continue
                validate(r["source"], r["text"])
                loc = json.loads(r["locator"])
                line = lines[loc["line"]]
                lines[loc["line"]] = (
                    line[: loc["start"]]
                    + json.dumps(r["text"], ensure_ascii=False)
                    + line[loc["end"] :]
                )
            output = "".join(lines)
        else:
            output = dump(
                [
                    {
                        "id": r["id"],
                        "source": r["source"],
                        "translation": r["text"],
                        "status": r["status"],
                        "speaker": r["speaker"],
                        "scene": r["scene"],
                    }
                    for r in records
                ]
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(
                target, target.with_name(target.name + ".backup-" + str(time.time_ns()))
            )
        target.write_text(output, encoding="utf-8")
        return str(target)
