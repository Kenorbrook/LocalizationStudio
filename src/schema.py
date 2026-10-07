"""SQLite schema initialization and compatibility migrations."""

import time


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY,name TEXT,root TEXT UNIQUE,settings TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,project INTEGER,path TEXT,kind TEXT,hash TEXT,original TEXT,UNIQUE(project,path));
    CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,file INTEGER,position INTEGER,source TEXT,text TEXT DEFAULT '',speaker TEXT,scene TEXT,locator TEXT,status TEXT DEFAULT 'empty',revision INTEGER DEFAULT 0,manual INTEGER DEFAULT 0,reviewer TEXT DEFAULT '',UNIQUE(file,position,source));
    CREATE INDEX IF NOT EXISTS records_file ON records(file,position);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,record INTEGER,revision INTEGER,text TEXT,status TEXT,reviewer TEXT,reason TEXT,at TEXT);
    CREATE INDEX IF NOT EXISTS history_record_revision ON history(record,revision);
    CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,project INTEGER,stage TEXT,provider TEXT,settings TEXT,state TEXT,done INTEGER DEFAULT 0,total INTEGER DEFAULT 0,current INTEGER,error TEXT DEFAULT '',pid INTEGER,heartbeat REAL DEFAULT 0,created TEXT);
    CREATE TABLE IF NOT EXISTS queue(job INTEGER,record INTEGER,state TEXT DEFAULT 'pending',PRIMARY KEY(job,record));
    CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY,project INTEGER,job INTEGER,record INTEGER,message TEXT,at TEXT,resolved INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY,record INTEGER,revision INTEGER,text TEXT,reason TEXT,reviewer TEXT,at TEXT,state TEXT DEFAULT 'pending');
    CREATE TABLE IF NOT EXISTS revision_snapshots(record INTEGER,revision INTEGER,text TEXT,status TEXT,manual INTEGER,reviewer TEXT,PRIMARY KEY(record,revision));
    CREATE TABLE IF NOT EXISTS mcp_connections(session TEXT PRIMARY KEY,pid INTEGER,client TEXT,version TEXT,heartbeat REAL,connected INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS mcp_requests(id TEXT PRIMARY KEY,session TEXT,job INTEGER,request TEXT,state TEXT DEFAULT 'pending',response TEXT DEFAULT '',created REAL);
    CREATE TABLE IF NOT EXISTS fragment_cache(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS fragment_plans(key TEXT PRIMARY KEY,parts TEXT);
    CREATE TABLE IF NOT EXISTS fragment_manifests(record INTEGER,source_hash TEXT,text_hash TEXT,parts TEXT,PRIMARY KEY(record,source_hash,text_hash));
    CREATE TABLE IF NOT EXISTS project_analysis(project INTEGER PRIMARY KEY,report TEXT,at TEXT);
    CREATE TABLE IF NOT EXISTS process_preferences(project INTEGER PRIMARY KEY,max_phrases INTEGER DEFAULT 200,max_seconds REAL DEFAULT 600,page_size INTEGER DEFAULT 50);
    CREATE TABLE IF NOT EXISTS language_checks(record INTEGER PRIMARY KEY,source_hash TEXT,base_language TEXT,result TEXT);
    CREATE TABLE IF NOT EXISTS preserved_records(record INTEGER PRIMARY KEY,kind TEXT,reason TEXT,previous_revision INTEGER);
    CREATE TABLE IF NOT EXISTS queue_overrides(job INTEGER,record INTEGER,settings TEXT,PRIMARY KEY(job,record));
    CREATE TABLE IF NOT EXISTS cache_owners(project INTEGER,key TEXT,PRIMARY KEY(project,key));
    CREATE TABLE IF NOT EXISTS id_counters(name TEXT PRIMARY KEY,next_id INTEGER);
    CREATE TABLE IF NOT EXISTS schema_versions(name TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS verification_snapshots(record INTEGER,revision INTEGER,before_status TEXT,mark_json TEXT DEFAULT '{}',PRIMARY KEY(record,revision),FOREIGN KEY(record) REFERENCES records(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS record_marks(record INTEGER PRIMARY KEY,kind TEXT,at REAL,was_manual INTEGER,protected_revision INTEGER);
    """)
    if "origin" not in {r[1] for r in db.execute("PRAGMA table_info(record_marks)")}:
        db.execute("ALTER TABLE record_marks ADD COLUMN origin TEXT DEFAULT 'unknown'")
    for column in ["completed", "keep_visible", "deleting"]:
        if column not in {r[1] for r in db.execute("PRAGMA table_info(projects)")}:
            db.execute(f"ALTER TABLE projects ADD COLUMN {column} INTEGER DEFAULT 0")
    for table in ["projects", "jobs", "files", "records"]:
        db.execute(
            "INSERT OR IGNORE INTO id_counters VALUES (?,?)",
            (
                table,
                db.execute(f"SELECT coalesce(max(id),0)+1 FROM {table}").fetchone()[0],
            ),
        )
    if "hidden" not in {r[1] for r in db.execute("PRAGMA table_info(projects)")}:
        db.execute("ALTER TABLE projects ADD COLUMN hidden INTEGER DEFAULT 0")
    if "preserve_override" not in {
        r[1] for r in db.execute("PRAGMA table_info(records)")
    }:
        db.execute("ALTER TABLE records ADD COLUMN preserve_override INTEGER DEFAULT 0")
    if "independent" not in {
        r[1] for r in db.execute("PRAGMA table_info(record_marks)")
    }:
        db.execute("ALTER TABLE record_marks ADD COLUMN independent INTEGER DEFAULT 1")
        db.execute(
            "UPDATE records SET manual=(SELECT was_manual FROM record_marks WHERE record=records.id) WHERE EXISTS(SELECT 1 FROM record_marks WHERE record=records.id AND protected_revision=records.revision)"
        )
    if "rate" not in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}:
        db.execute("ALTER TABLE jobs ADD COLUMN rate REAL DEFAULT 0")
    columns = {r[1] for r in db.execute("PRAGMA table_info(mcp_connections)")}
    if "sampling" not in columns:
        db.execute("ALTER TABLE mcp_connections ADD COLUMN sampling INTEGER DEFAULT 0")
    if "model" not in columns:
        db.execute("ALTER TABLE mcp_connections ADD COLUMN model TEXT DEFAULT ''")
    job_columns = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
    for name, definition in [
        ("run_started", "REAL DEFAULT 0"),
        ("run_elapsed", "REAL DEFAULT 0"),
        ("run_done", "INTEGER DEFAULT 0"),
    ]:
        if name not in job_columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    if "worker_active" not in job_columns:
        db.execute("ALTER TABLE jobs ADD COLUMN worker_active INTEGER DEFAULT 0")
    if "budget_json" not in {r[1] for r in db.execute("PRAGMA table_info(errors)")}:
        db.execute("ALTER TABLE errors ADD COLUMN budget_json TEXT DEFAULT '{}'")
    for name, definition in [
        ("fragment_done", "INTEGER DEFAULT 0"),
        ("fragment_total", "INTEGER DEFAULT 0"),
        ("fragment_preview", "TEXT DEFAULT ''"),
    ]:
        if name not in job_columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    if "finished_at" not in {r[1] for r in db.execute("PRAGMA table_info(queue)")}:
        db.execute("ALTER TABLE queue ADD COLUMN finished_at REAL DEFAULT 0")
        old = db.execute(
            "SELECT q.job,q.record,coalesce(max(h.at),j.created) at FROM queue q JOIN jobs j ON j.id=q.job LEFT JOIN history h ON h.record=q.record AND h.at>=j.created WHERE q.state='done' GROUP BY q.job,q.record"
        ).fetchall()
        for row in old:
            try:
                stamp = time.mktime(time.strptime(row["at"], "%Y-%m-%d %H:%M:%S"))
            except (ValueError, TypeError):
                stamp = time.time()
            db.execute(
                "UPDATE queue SET finished_at=? WHERE job=? AND record=?",
                (stamp, row["job"], row["record"]),
            )
    if "priority" not in {r[1] for r in db.execute("PRAGMA table_info(queue)")}:
        db.execute("ALTER TABLE queue ADD COLUMN priority INTEGER DEFAULT 0")
    if "at" not in {r[1] for r in db.execute("PRAGMA table_info(preserved_records)")}:
        db.execute("ALTER TABLE preserved_records ADD COLUMN at REAL DEFAULT 0")
        db.execute(
            "UPDATE preserved_records SET at=coalesce((SELECT max(CAST(strftime('%s',h.at) AS REAL)) FROM history h WHERE h.record=preserved_records.record AND h.status='preserved'),0)"
        )
    db.executescript(
        """CREATE TRIGGER IF NOT EXISTS queue_finished_timestamp AFTER UPDATE OF state ON queue
        WHEN OLD.state='pending' AND NEW.state IN ('done','error','protected')
        BEGIN UPDATE queue SET finished_at=CAST(strftime('%s','now') AS REAL) WHERE job=NEW.job AND record=NEW.record; END;"""
    )

    if not db.execute(
        "SELECT 1 FROM schema_versions WHERE name='human-verification-marks-v1'"
    ).fetchone():
        # Repair marks that existed when a legacy human verification was saved.
        rows = db.execute(
            "SELECT r.id,r.revision,h.revision checked_revision,s.status before_status,m.* FROM records r JOIN history h ON h.record=r.id AND h.revision=(SELECT max(revision) FROM history WHERE record=r.id AND status='verified' AND reviewer LIKE 'human%') LEFT JOIN revision_snapshots s ON s.record=r.id AND s.revision=h.revision-1 JOIN record_marks m ON m.record=r.id WHERE r.status='verified' AND r.reviewer LIKE 'human%' AND m.protected_revision<=h.revision"
        ).fetchall()
        import json

        for row in rows:
            mark = {
                key: row[key]
                for key in [
                    "kind",
                    "at",
                    "was_manual",
                    "protected_revision",
                    "origin",
                    "independent",
                ]
            }
            db.execute(
                "INSERT OR IGNORE INTO verification_snapshots VALUES (?,?,?,?)",
                (
                    row["id"],
                    row["revision"],
                    row["before_status"] or "translated",
                    json.dumps(mark),
                ),
            )
            db.execute("DELETE FROM record_marks WHERE record=?", (row["id"],))
        db.execute(
            "INSERT OR IGNORE INTO schema_versions VALUES ('human-verification-marks-v1')"
        )
