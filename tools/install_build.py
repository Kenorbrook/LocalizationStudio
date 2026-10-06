"""Install a verified local build after installed_control pause and GUI shutdown."""

import json
import sqlite3
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from update_installer import replace_application
from version import VERSION

reports = root / "reports"
reports.mkdir(exist_ok=True)
backup = root / "data/backups" / ("update-" + time.strftime("%Y%m%d_%H%M%S"))
backup.mkdir(parents=True)
with (
    sqlite3.connect(root / "data/studio.sqlite3") as db,
    sqlite3.connect(backup / "studio.sqlite3") as target,
):
    db.backup(target)
    before = {
        "projects": db.execute("SELECT count(*) FROM projects").fetchone()[0],
        "records": db.execute("SELECT count(*) FROM records").fetchone()[0],
        "jobs": [
            dict(zip(["id", "state", "done", "total"], row))
            for row in db.execute("SELECT id,state,done,total FROM jobs")
        ],
    }
replace_application(
    {
        "home": str(root),
        "bundle": str(root / "release/LocalizationStudio"),
        "backup": str(backup),
        "version": VERSION,
    }
)
report = {
    "backup": str(backup),
    "worker": "LocalizationWorker.exe",
    "version": VERSION,
    "before": before,
}
(reports / "update-install.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False))
