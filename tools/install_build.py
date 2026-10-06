import json, shutil, sqlite3, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
reports = root / "reports"
reports.mkdir(exist_ok=True)
stamp = time.strftime("%Y%m%d_%H%M%S")
backup = root / "data" / "backups" / ("update-" + stamp)
backup.mkdir(parents=True)
with (
    sqlite3.connect(root / "data" / "studio.sqlite3") as db,
    sqlite3.connect(backup / "studio.sqlite3") as target,
):
    db.backup(target)
    before = {
        "projects": db.execute("SELECT count(*) FROM projects").fetchone()[0],
        "records": db.execute("SELECT count(*) FROM records").fetchone()[0],
        "jobs": [
            dict(zip(["id", "state", "done", "total"], r))
            for r in db.execute("SELECT id,state,done,total FROM jobs")
        ],
    }
resources = [
    "index.html",
    "ui_extensions.js",
    "project_ui.js",
    "workflow_ui.js",
    "detection_ui.js",
    "process_ui.js",
]
for name in ["LocalizationStudio.exe", "worker_version.txt", "mcp_config.json"]:
    if (root / name).exists():
        shutil.copy2(root / name, backup / name)
(backup / "_internal").mkdir()
for name in resources:
    if (root / "_internal" / name).exists():
        shutil.copy2(root / "_internal" / name, backup / "_internal" / name)
if (root / "_internal" / "ui").exists():
    shutil.copytree(root / "_internal" / "ui", backup / "_internal" / "ui")
release = root / "release" / "LocalizationStudio"
worker = f"LocalizationWorker_{stamp}.exe"
shutil.copy2(release / "LocalizationStudio.exe", root / "LocalizationStudio.exe")
shutil.copy2(release / "LocalizationWorker.exe", root / worker)
shutil.copytree(
    release / "_internal" / "ui", root / "_internal" / "ui", dirs_exist_ok=True
)
(root / "worker_version.txt").write_text(worker, encoding="utf-8")
config = json.loads((root / "mcp_config.json").read_text(encoding="utf-8-sig"))
config["mcpServers"]["localization-studio"]["command"] = str(root / worker)
(root / "mcp_config.json").write_text(
    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
)
report = {"backup": str(backup), "worker": worker, "before": before}
(reports / "update-install.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False))
