"""Bridge worker requests to an initialized stdio client advertising sampling."""

import json, time, uuid
from core import Store, dump


def generate(settings, prompt, entries):
    store = Store(settings["_studio_db"])
    session = settings["mcp_session"]
    jid = settings["_studio_job"]
    from mcp_server import connections

    if not any(c["session"] == session and c["sampling"] for c in connections(store)):
        raise RuntimeError("MCP-клиент отключён или не поддерживает sampling")
    params = {
        "systemPrompt": prompt,
        "messages": [
            {
                "role": "user",
                "content": {"type": "text", "text": dump({"items": entries})},
            }
        ],
        "maxTokens": int(settings.get("max_output", 1200)),
    }
    if settings.get("mcp_model_hint"):
        params["modelPreferences"] = {"hints": [{"name": settings["mcp_model_hint"]}]}
    rid = "studio-sampling-" + uuid.uuid4().hex
    with store.db() as db:
        db.execute(
            "INSERT INTO mcp_requests(id,session,job,request,created) VALUES (?,?,?,?,?)",
            (rid, session, jid, dump(params), time.time()),
        )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with store.db() as db:
            row = db.execute(
                "SELECT state,response FROM mcp_requests WHERE id=?", (rid,)
            ).fetchone()
            state = db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()
        if row["state"] == "done":
            result = json.loads(row["response"])
            content = result.get("content", {})
            if result.get("stopReason") == "maxTokens":
                from long_text import OutputTruncated

                raise OutputTruncated("MCP-клиент достиг лимита ответа")
            if isinstance(content, list):
                text = "\n".join(
                    c.get("text", "") for c in content if c.get("type") == "text"
                )
            else:
                text = content.get("text", "") if content.get("type") == "text" else ""
            if not text:
                raise ValueError("MCP-клиент вернул ответ без текста")
            return text, str(result.get("model", ""))
        if row["state"] == "failed":
            raise RuntimeError(row["response"] or "MCP-запрос не выполнен")
        if not state or state[0] != "running":
            break
        if not any(c["session"] == session for c in connections(store)):
            break
        time.sleep(0.3)
    with store.db() as db:
        db.execute(
            "UPDATE mcp_requests SET state='failed',response='Запрос прекращён или истекло время ожидания' WHERE id=? AND state IN ('pending','sent')",
            (rid,),
        )
    raise RuntimeError("MCP-запрос не завершён; очередь сохранена")
