"""Executed by Hermes' own Python, never by the frozen Studio interpreter."""

import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, sys.argv[2])
import hermes_bootstrap
from run_agent import AIAgent

if request.get("check"):
    Path(request["result"]).write_text(json.dumps({"ready": True}), encoding="utf-8")
else:
    base_url = request.get("base_url", "http://127.0.0.1:11434/v1")
    if urlparse(base_url).hostname != "127.0.0.1":
        raise ValueError("Hermes plugin only uses a local model")
    from hermes_cli.mcp_startup import ensure_mcp_discovery_before_agent_build

    ensure_mcp_discovery_before_agent_build(
        logger=logging.getLogger(__name__), single_query=True
    )
    agent = AIAgent(
        model=request["model"],
        provider="custom",
        api_key="ollama",
        base_url=base_url,
        enabled_toolsets=["mcp-studio"],
        max_iterations=8,
        quiet_mode=True,
        skip_memory=True,
        load_soul_identity=False,
        skip_context_files=True,
        skip_background_review=True,
        run_budget_seconds=240,
        request_overrides={
            "max_tokens": 1600,
        },
    )
    try:
        result = agent.run_conversation(request["prompt"])
        text = result.get("final_response", "")
        if not text or result.get("failed") or result.get("partial"):
            raise RuntimeError(
                "Hermes не завершил исследование; проверьте журнал и поддержку инструментов моделью"
            )
        Path(request["result"]).write_text(
            json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8"
        )
    finally:
        agent.close()
        from tools.mcp_tool_lifecycle import shutdown_mcp_servers

        shutdown_mcp_servers()
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
