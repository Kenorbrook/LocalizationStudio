"""Exercise the real pinned optional installer in a neutral workspace."""

import argparse
import json
import os
import shutil
import stat
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from hermes_plugin import HermesPlugin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.cleanup:
        if args.install or args.exercise:
            parser.error(
                "Cleanup must be a separate invocation after all fixture processes have exited"
            )
        cleanup()
        return
    store = Store(ROOT / "qa-hermes/runtime/test.sqlite3")
    plugin = HermesPlugin(store)
    if args.install:
        plugin.install()
        while plugin.busy:
            time.sleep(1)
    if args.exercise:
        exercise(plugin, store)
        check_cancel(plugin)
    result = plugin.snapshot()
    (ROOT / "reports/hermes-runtime-result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps({key: result[key] for key in ["installed", "phase", "message"]}),
        flush=True,
    )
    if args.install and not result["installed"]:
        raise RuntimeError(result["message"])


def cleanup():
    from update_package import reject_links

    fixture = ROOT / "qa-hermes" / "runtime"
    if fixture.resolve() != ROOT.resolve() / "qa-hermes" / "runtime":
        raise RuntimeError("Fixture path escaped the workspace")
    for parent in [fixture, *fixture.parents]:
        if parent.is_symlink() or parent.is_junction():
            raise RuntimeError("Linked fixture ancestor")
    if not fixture.exists():
        return
    # PM deliberately creates links. Remove each link itself before descending.
    removed = 0
    for folder, dirs, files in os.walk(fixture, topdown=True, followlinks=False):
        for name in list(dirs):
            path = Path(folder) / name
            if path.is_symlink() or path.is_junction():
                dirs.remove(name)
                if path.is_junction():
                    os.rmdir(path)
                else:
                    path.unlink()
                removed += 1
        for name in files:
            path = Path(folder) / name
            if path.is_symlink():
                path.unlink()
                removed += 1
    reject_links(fixture)

    def writable(function, path, error):
        os.chmod(path, stat.S_IWRITE)
        function(path)

    shutil.rmtree(fixture, onerror=writable)
    print(
        json.dumps(
            {"fixture_removed": True, "links_unlinked_without_traversal": removed}
        )
    )


def check_cancel(plugin):
    plugin._launch(
        "checking",
        lambda: plugin._run([sys.executable, "-c", "import time;time.sleep(60)"]),
    )
    deadline = time.monotonic() + 10
    while plugin.process is None and time.monotonic() < deadline:
        time.sleep(0.05)
    process = plugin.process
    assert process is not None
    plugin.cancel()
    while plugin.busy and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not plugin.busy and process.poll() is not None
    assert not plugin.store.hermes_busy and not plugin.store.hermes_inference
    print(json.dumps({"owned_process_cancelled": True}), flush=True)


def exercise(plugin, store):
    """Real Hermes + real Studio MCP, deterministic local LLM protocol fixture."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = {
                "object": "list",
                "data": [
                    {"id": "fixture-agent", "object": "model", "owned_by": "local"}
                ],
            }
            self.respond(data)

        def do_POST(self):
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            if not self.path.endswith("chat/completions"):
                return self.respond(
                    {
                        "capabilities": ["tools"],
                        "model_info": {"llama.context_length": 65536},
                    }
                )
            calls.append(payload)
            (ROOT / "reports/hermes-last-llm-request.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            tool_messages = [
                message for message in payload["messages"] if message["role"] == "tool"
            ]
            if not tool_messages:
                tool = next(
                    item["function"]["name"]
                    for item in payload["tools"]
                    if "inspect_game_project" in item["function"]["name"]
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "fixture_tool",
                            "type": "function",
                            "function": {"name": tool, "arguments": "{}"},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                assert "script.rpy" in json.dumps(tool_messages), tool_messages
                message = {
                    "role": "assistant",
                    "content": "Ren'Py: script.rpy. Evidence obtained through the project MCP tools.",
                }
                finish = "stop"
            if payload.get("stream"):
                delta = dict(message)
                for index, item in enumerate(delta.get("tool_calls", [])):
                    item["index"] = index
                frames = [
                    {
                        "id": "fixture",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "fixture-agent",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": None}
                        ],
                    },
                    {
                        "id": "fixture",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "fixture-agent",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    },
                ]
                body = (
                    "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
                    + "data: [DONE]\n\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.respond(
                    {
                        "id": "fixture",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "fixture-agent",
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": finish}
                        ],
                        "usage": {
                            "prompt_tokens": 50,
                            "completion_tokens": 10,
                            "total_tokens": 60,
                        },
                    }
                )

        def respond(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    game = store.path.parent / "game"
    game.mkdir(exist_ok=True)
    (game / "script.rpy").write_text(
        'label start:\n    "A neutral sentence."\n', encoding="utf-8"
    )
    project = store.project(str(game))["id"]
    plugin.configure({"enabled": True, "model": "fixture-agent", "context": 65536})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    original = plugin._request
    plugin._request = lambda payload: original(
        {**payload, "base_url": f"http://127.0.0.1:{server.server_port}/v1"}
    )
    try:
        plugin.run(project, "research")
        deadline = time.monotonic() + 180
        while plugin.busy and time.monotonic() < deadline:
            time.sleep(0.5)
        result = plugin.snapshot(project)
        assert (
            result["phase"] == "ready"
            and len(calls) >= 2
            and "script.rpy" in result["report"]
        ), result
        print(
            json.dumps(
                {
                    "real_hermes_mcp_loop": True,
                    "local_llm_calls": len(calls),
                    "gpu_inference": False,
                }
            ),
            flush=True,
        )
    finally:
        plugin.cancel()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
