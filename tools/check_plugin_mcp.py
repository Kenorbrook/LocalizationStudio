"""Exercise the packaged scoped MCP with neutral files and no model inference."""

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from update_package import reject_links


def main():
    parent = ROOT / "qa-knowledge"
    fixture = parent / ("plugin-mcp-" + uuid.uuid4().hex)
    fixture.mkdir(parents=True)
    try:
        store = Store(fixture / "test.sqlite3")
        game = fixture / "game"
        game.mkdir()
        (game / "script.rpy").write_text('label start:\n    "A neutral line."\n')
        project = store.project(str(game))["id"]
        requests = [
            {"id": 1, "method": "initialize", "params": {"capabilities": {}}},
            {"id": 2, "method": "tools/list"},
            {
                "id": 3,
                "method": "tools/call",
                "params": {"name": "inspect_game_project", "arguments": {}},
            },
            {
                "id": 4,
                "method": "tools/call",
                "params": {"name": "start_job", "arguments": {}},
            },
        ]
        result = subprocess.run(
            [
                str(ROOT / "release/LocalizationStudio/LocalizationWorker.exe"),
                "--db",
                str(store.path),
                "--mcp",
                "--agent-project",
                str(project),
            ],
            input="".join(
                json.dumps({"jsonrpc": "2.0", **request}) + "\n" for request in requests
            ),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        responses = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith("{")
        ]
        assert len(responses) == 4, responses
        tools = {tool["name"] for tool in responses[1]["result"]["tools"]}
        assert tools == {
            "inspect_game_project",
            "read_game_text",
            "translation_errors",
            "retry_plan",
        }, tools
        assert not responses[2]["result"].get("isError"), responses[2]
        assert "script.rpy" in json.dumps(responses[2]), responses[2]
        assert responses[3]["result"].get("isError"), responses[3]
        report = dict(
            passed=True,
            packaged=True,
            project_scoped_tools=len(tools),
            neutral_read=True,
            write_denied=True,
            model_requests=0,
        )
        (ROOT / "reports/v060-plugin-mcp.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        if fixture.resolve().parent != parent.resolve():
            raise ValueError("Invalid fixture path")
        reject_links(fixture)
        shutil.rmtree(fixture)


if __name__ == "__main__":
    main()
