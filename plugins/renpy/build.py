"""Stage translated files and reversible language integration in an owned copy."""

import hashlib
import json
import uuid
from pathlib import Path

from .filesystem import guard, owned, write_json
from .integration import plan_controls
from validation import validate
from plugin_package import safe_path


def within(root, relative):
    relative = safe_path(relative)
    path = guard(Path(root) / relative)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Файл находится вне подготовленной копии")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def build_plan(store, project, state, home, mode):
    root = owned(state["copy"], state["owner"])
    prior = state.get("built_hashes") or {}
    for relative, expected in prior.items():
        path = within(root, relative)
        if digest(path) != expected:
            raise ValueError("Файл подготовленной копии изменён: " + relative)
    with store.db() as db:
        files = [
            dict(f)
            for f in db.execute("SELECT * FROM files WHERE project=?", (project,))
        ]
        records = [
            dict(r)
            for r in db.execute(
                "SELECT r.* FROM records r JOIN files f ON f.id=r.file WHERE f.project=?",
                (project,),
            )
        ]
        errors = db.execute(
            "SELECT count(*) FROM errors WHERE project=? AND resolved=0", (project,)
        ).fetchone()[0]
        marks = db.execute(
            "SELECT count(*) FROM record_marks m JOIN records r ON r.id=m.record JOIN files f ON f.id=r.file WHERE f.project=?",
            (project,),
        ).fetchone()[0]
    if (
        not files
        or not records
        or any(r["status"] == "empty" or not r["text"] for r in records)
    ):
        raise ValueError("Сначала завершите перевод всех подготовленных строк")
    if errors or marks:
        raise ValueError("Сначала разберите ошибки, ручную проверку и брак проекта")
    if any(r["status"] == "translated" and not r["manual"] for r in records):
        raise ValueError("Сначала выполните локальную редактуру переведённых строк")
    for record in records:
        validate(record["source"], record["text"])
    output = {}
    temporary = guard(Path(home) / "exports" / uuid.uuid4().hex)
    temporary.mkdir(parents=True)
    for file in files:
        target = guard(Path(file["path"]))
        if (
            not target.resolve().is_relative_to(root / "game/tl/studio_russian")
            or file["kind"] != "renpy"
        ):
            raise ValueError(
                "В рабочем проекте обнаружен файл вне подготовленного слоя"
            )
        relative = target.relative_to(root).as_posix()
        if (
            relative not in prior
            and hashlib.sha256(
                target.read_text(encoding="utf-8-sig").encode()
            ).hexdigest()
            != file["hash"]
        ):
            raise ValueError("Шаблон изменён вне Studio: " + relative)
        exported = temporary / (str(file["id"]) + ".rpy")
        store.export(file["id"], str(exported))
        output[relative] = exported.read_bytes()
    output.update(plan_controls(root, mode, prior))
    # Remove an earlier mode's preferences insertion when switching to launcher.
    if mode == "launcher":
        for relative in prior:
            if (
                relative.endswith(".rpy")
                and not relative.startswith("game/tl/")
                and relative != "game/zz_studio_localization.rpy"
            ):
                path = within(root, relative)
                text = path.read_text(encoding="utf-8-sig")
                output[relative] = text.replace(
                    "    use _studio_language_selector\n", ""
                ).encode()
    return output


def apply_plan(root, output, home):
    root = Path(root)
    snapshot = guard(Path(home) / "builds" / uuid.uuid4().hex)
    snapshot.mkdir(parents=True)
    before = {}
    for relative in output:
        path = within(root, relative)
        if path.is_file():
            backup = within(snapshot, relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(path.read_bytes())
            before[relative] = True
        else:
            before[relative] = False
    write_json(snapshot / "snapshot.json", before)
    staged, applied = {}, {}
    try:
        for relative, data in output.items():
            path = within(root, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = guard(path.with_name(path.name + ".studio-" + uuid.uuid4().hex))
            staged[relative] = temporary
            temporary.write_bytes(data)
        for relative, temporary in staged.items():
            path = within(root, relative)
            original = digest(within(snapshot, relative)) if before[relative] else None
            if digest(path) != original:
                raise ValueError("Файл изменён во время сборки: " + relative)
            temporary.replace(path)
            applied[relative] = digest(path)
    except Exception:
        restore(root, snapshot, applied)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return str(snapshot), {relative: digest(root / relative) for relative in output}


def restore(root, snapshot, expected):
    root, snapshot = guard(root), guard(snapshot)
    before = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    for relative, checksum in expected.items():
        if digest(within(root, relative)) != checksum:
            raise ValueError("Откат остановлен: файл изменён вне Studio: " + relative)
    for relative in expected:
        existed = before[relative]
        path = within(root, relative)
        if existed:
            path.write_bytes(within(snapshot, relative).read_bytes())
        else:
            path.unlink(missing_ok=True)
        # Only compiled counterparts of files changed by this owned patch.
        if path.suffix == ".rpy":
            path.with_suffix(".rpyc").unlink(missing_ok=True)
