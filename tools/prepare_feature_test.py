import sys, json, subprocess, time, argparse

sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--source", action="store_true")
args = parser.parse_args()
subprocess.run(
    ["node", "--check", str(root / "tools/project_feature_probe.js")], check=True
)
reports = root / "reports"
reports.mkdir(exist_ok=True)
sys.path.insert(0, str(root / "src"))
from core import Store

fixture = root / "qa-project-features" / str(time.time_ns())
fixture.mkdir(parents=True)
db = fixture / "test.sqlite3"
store = Store(db)
pid = store.project(str(fixture))["id"]
path = fixture / "corpus.json"
path.write_text(
    json.dumps(
        [
            {"source": f"Line {i}", "translation": "Текст" if i == 0 else ""}
            for i in range(52)
        ]
    ),
    encoding="utf-8",
)
store.import_files(pid, [path])
jid = store.create_job(pid, "translate", "local", {})
store.error(jid, 3, "Не помещается в контекст — тест")
with store.db() as connection:
    fixture_error = connection.execute("SELECT id FROM errors LIMIT 1").fetchone()[0]
    connection.executemany(
        "INSERT INTO errors(project,job,record,message,at,budget_json) SELECT project,job,record,message,at,budget_json FROM errors WHERE id=?",
        [(fixture_error,)] * 100,
    )
for rid in (51, 52):
    store.update(rid, 0, "Текст " + str(rid), "translated", "local:fixture")
    with store.db() as connection:
        connection.execute(
            "UPDATE queue SET state='done' WHERE job=? AND record=?", (jid, rid)
        )
with store.db() as connection:
    connection.execute("UPDATE jobs SET done=2 WHERE id=?", (jid,))
with store.db() as connection:
    connection.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), jid))
report = fixture / "report.json"
exe = root / "release" / "LocalizationStudio" / "LocalizationStudio.exe"
worker = exe.with_name("LocalizationWorker.exe")
client = subprocess.Popen(
    [str(worker), "--mcp", "--db", str(db)],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
client.stdin.write(
    (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "UI test client", "version": "1.0"}},
            }
        )
        + "\n"
    ).encode()
)
client.stdin.flush()
assert "result" in json.loads(client.stdout.readline())
gui = subprocess.Popen(
    [
        *(
            [sys.executable, str(root / "src/desktop.py")]
            if args.source
            else [str(exe)]
        ),
        "--db",
        str(db),
        "--smoke-report",
        str(report),
        "--ui-test-script",
        str(root / "tools" / "project_feature_probe.js"),
    ]
)
try:
    for i in range(160):
        if report.exists():
            break
        time.sleep(0.5)
    result = json.loads(report.read_text(encoding="utf-8"))
    (reports / "project-feature-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    assert result.get("passed"), result
finally:
    client.stdin.close()
    client.wait(timeout=5)
    if gui.poll() is None:
        gui.terminate()
        gui.wait(timeout=15)
    import shutil

    if fixture.resolve().parent != (root / "qa-project-features").resolve():
        raise RuntimeError("Unexpected fixture location")
    shutil.rmtree(fixture)
