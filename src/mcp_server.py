"""Minimal stdio MCP server; proposals require explicit acceptance in the UI."""

import argparse, json, sys, os, time, uuid, threading, ctypes
from core import Store, dump, now, validate
from paths import DEFAULT_DB
from queries import state, records


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


INT = {"type": "integer"}
STR = {"type": "string"}
TOOLS = [
    {
        "name": "list_projects",
        "description": "List imported game projects. Read only.",
        "inputSchema": schema({}),
    },
    {
        "name": "project_status",
        "description": "Files, counts and job progress for one project.",
        "inputSchema": schema({"project": INT}, ["project"]),
    },
    {
        "name": "read_block",
        "description": "Read up to 50 source/translation records. Use offset for pagination.",
        "inputSchema": schema(
            {"file": INT, "offset": INT, "search": STR, "status": STR}, ["file"]
        ),
    },
    {
        "name": "read_record",
        "description": "Read current version and source context for one record.",
        "inputSchema": schema({"id": INT}, ["id"]),
    },
    {
        "name": "propose_translation",
        "description": "Propose a translation or review with evidence. Does NOT overwrite the record; user accepts in Studio. Always supply current revision.",
        "inputSchema": schema(
            {"id": INT, "revision": INT, "text": STR, "reason": STR, "reviewer": STR},
            ["id", "revision", "text", "reason", "reviewer"],
        ),
    },
]
SESSION = uuid.uuid4().hex
INITIALIZED = threading.Event()


def alive(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32")
        kernel.OpenProcess.restype = ctypes.c_void_p
        h = kernel.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        ok = kernel.GetExitCodeProcess(h, ctypes.byref(code))
        kernel.CloseHandle(h)
        return bool(ok and code.value == 259)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def connections(store):
    with store.db() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM mcp_connections WHERE connected=1 AND heartbeat>?",
                (time.time() - 45,),
            )
        ]
    return [{**r, "transport": "stdio", "port": None} for r in rows if alive(r["pid"])]


def serve_stdio(store):
    stop = threading.Event()
    output_lock = threading.Lock()

    def send(message):
        with output_lock:
            print(dump(message), flush=True)

    def pulse():
        heartbeat = 0
        while not stop.wait(0.3):
            with store.db() as db:
                if time.time() - heartbeat >= 10:
                    db.execute(
                        "UPDATE mcp_connections SET heartbeat=? WHERE session=?",
                        (time.time(), SESSION),
                    )
                    heartbeat = time.time()
                requests = []
                if INITIALIZED.is_set():
                    requests = [
                        dict(r)
                        for r in db.execute(
                            "SELECT q.* FROM mcp_requests q JOIN jobs j ON j.id=q.job JOIN mcp_connections c ON c.session=q.session WHERE q.session=? AND q.state='pending' AND j.state='running' AND c.sampling=1 ORDER BY q.created LIMIT 1",
                            (SESSION,),
                        )
                    ]
                    for r in requests:
                        db.execute(
                            "UPDATE mcp_requests SET state='sent' WHERE id=? AND state='pending'",
                            (r["id"],),
                        )
            for r in requests:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": r["id"],
                        "method": "sampling/createMessage",
                        "params": json.loads(r["request"]),
                    }
                )

    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if (
                    "method" not in message
                    and isinstance(message.get("id"), str)
                    and message["id"].startswith("studio-sampling-")
                ):
                    with store.db() as db:
                        if "error" in message:
                            db.execute(
                                "UPDATE mcp_requests SET state='failed',response='MCP-клиент отклонил запрос' WHERE id=? AND session=? AND state='sent'",
                                (message["id"], SESSION),
                            )
                        else:
                            result = message.get("result", {})
                            updated = db.execute(
                                "UPDATE mcp_requests SET state='done',response=? WHERE id=? AND session=? AND state='sent'",
                                (dump(result), message["id"], SESSION),
                            )
                            if updated.rowcount:
                                db.execute(
                                    "UPDATE mcp_connections SET model=? WHERE session=?",
                                    (str(result.get("model", ""))[:200], SESSION),
                                )
                    continue
                response = handle(store, message)
            except Exception:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            if response is not None:
                send(response)
    finally:
        stop.set()
        thread.join(timeout=2)
        with store.db() as db:
            db.execute(
                "UPDATE mcp_connections SET connected=0 WHERE session=?", (SESSION,)
            )
            db.execute(
                "UPDATE mcp_requests SET state='failed',response='MCP-клиент отключён' WHERE session=? AND state IN ('pending','sent')",
                (SESSION,),
            )


def call(store, name, args):
    if name == "list_projects":
        with store.db() as db:
            return [dict(r) for r in db.execute("SELECT id,name,root FROM projects")]
    if name == "project_status":
        return state(store, int(args["project"]))
    if name == "read_block":
        return records(
            store,
            int(args["file"]),
            int(args.get("offset", 0)),
            args.get("search", ""),
            args.get("status", ""),
        )
    if name == "read_record":
        r = store.record(int(args["id"]))
        from speaker_context import context_entry

        entry = context_entry(store, r["id"])
        r["context"] = entry["context_before"] + entry["context_after"]
        r["speaker_metadata"] = entry["speaker_metadata"]
        r["confirmed_character_examples"] = entry["confirmed_character_examples"]
        return r
    if name == "propose_translation":
        r = store.record(int(args["id"]))
        validate(r["source"], args["text"])
        if r["revision"] != int(args["revision"]):
            raise ValueError("Version changed; reread record")
        if not args["reason"].strip() or not args["reviewer"].strip():
            raise ValueError("Reason and reviewer are required")
        with store.db() as db:
            pid = db.execute(
                "INSERT INTO proposals(record,revision,text,reason,reviewer,at) VALUES (?,?,?,?,?,?)",
                (
                    r["id"],
                    r["revision"],
                    args["text"],
                    args["reason"],
                    args["reviewer"],
                    now(),
                ),
            ).lastrowid
        return {"proposal": pid, "state": "pending_user_acceptance"}
    raise ValueError("Unknown tool")


def handle(store, request):
    method = request.get("method")
    rid = request.get("id")
    params = request.get("params", {})
    if method == "notifications/initialized":
        INITIALIZED.set()
        return None
    if rid is None:
        return None
    try:
        if method == "initialize":
            client = params.get("clientInfo", {})
            with store.db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO mcp_connections(session,pid,client,version,heartbeat,connected,sampling) VALUES (?,?,?,?,?,1,?)",
                    (
                        SESSION,
                        os.getpid(),
                        str(client.get("name", "Неизвестный MCP-клиент"))[:200],
                        str(client.get("version", ""))[:80],
                        time.time(),
                        int("sampling" in params.get("capabilities", {})),
                    ),
                )
            supported = ["2025-03-26", "2025-06-18", "2025-11-25"]
            version = params.get("protocolVersion")
            result = {
                "protocolVersion": version if version in supported else "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "localization-studio", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": dump(
                                call(store, params["name"], params.get("arguments", {}))
                            ),
                        }
                    ]
                }
            except Exception as e:
                result = {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32602, "message": str(e)},
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    store = Store(args.db)
    serve_stdio(store)
