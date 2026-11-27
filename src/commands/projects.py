"""Projects command handlers."""

import time
from pathlib import Path


def complete_project(store, data):
    return store.complete_project(int(data["project"]))


def delete_project(store, data):
    from project_lifecycle import delete_project

    return delete_project(store, int(data["project"]))


def hide_project(store, data):
    return store.hide_project(int(data["project"]), True)


def restore_project(store, data):
    return store.hide_project(int(data["project"]), False)


def project(store, data):
    from game_detection import inspect_project, import_detected

    p, status = store.project(data["root"], with_status=True)
    if status != "created":
        return {
            "project": p,
            "status": status,
            "import": {"files": 0, "added": 0, "errors": []},
        }
    report = inspect_project(store, p["id"])
    paths = report["auto_import"]
    if data.get("scan") and report["engine"]["id"] not in {"multiple"}:
        paths = paths + [
            item["path"]
            for item in report["candidates"]
            if "/" not in item["path"]
            and item["supported"]
            and item["role"] in {"corpus", "text"}
        ]
    result = import_detected(store, p["id"], paths)
    return {
        "project": p,
        "status": status,
        "import": result,
        "analysis": {"engine": report["engine"], "partial": report["partial"]},
    }


def analyze_project(store, data):
    from game_detection import inspect_project

    return inspect_project(store, int(data["project"]))


def import_detected(store, data):
    from game_detection import import_detected

    return import_detected(store, int(data["project"]), data["paths"])


def import_files(store, data):
    return store.import_files(int(data["project"]), data["paths"])


def upload(store, data):
    from project_workspace import ensure, inside

    folder = inside(ensure(store, int(data["project"])), "imports")
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in data["files"]:
        name = Path(f["name"]).name
        if Path(name).suffix.lower() not in {".txt", ".json", ".jsonl", ".csv", ".rpy"}:
            raise ValueError("Неподдерживаемый файл")
        dest = inside(folder, name)
        content = f["text"]
        if dest.exists() and dest.read_text(encoding="utf-8") != content:
            dest = inside(folder, str(time.time_ns()) + "-" + name)
        if not dest.exists():
            dest.write_text(content, encoding="utf-8")
        paths.append(str(dest))
    return store.import_files(int(data["project"]), paths)


def export(store, data):
    return {"path": store.export(int(data["file"]), data["destination"])}
