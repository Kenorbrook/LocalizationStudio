"""Exercise real Ollama create/show in an isolated store with a metadata-only GGUF."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hermes_models import HermesModels, configured_context
from hermes_install import stop_owned
from update_package import reject_links


def metadata_gguf():
    def string(value):
        data = value.encode()
        return struct.pack("<Q", len(data)) + data

    values = {
        "general.architecture": "llama",
        "general.name": "Neutral metadata fixture",
        "llama.context_length": 131072,
        "llama.embedding_length": 8,
        "llama.block_count": 1,
        "llama.attention.head_count": 1,
        "llama.feed_forward_length": 8,
        "llama.attention.layer_norm_rms_epsilon": 0.00001,
        "tokenizer.ggml.model": "llama",
        "tokenizer.ggml.tokens": ["<unk>", "<s>", "</s>"],
    }
    result = struct.pack("<4sIQQ", b"GGUF", 3, 0, len(values))
    for name, value in values.items():
        result += string(name)
        kind = (
            8
            if isinstance(value, str)
            else 6 if isinstance(value, float) else 9 if isinstance(value, list) else 4
        )
        result += struct.pack("<I", kind)
        if kind == 8:
            result += string(value)
        elif kind == 6:
            result += struct.pack("<f", value)
        elif kind == 9:
            result += struct.pack("<IQ", 8, len(value)) + b"".join(
                string(item) for item in value
            )
        else:
            result += struct.pack("<I", value)
    return result + b"\0" * (-len(result) % 32)


def main():
    executable = shutil.which("ollama") or str(
        Path(os.environ["LOCALAPPDATA"]) / "Programs/Ollama/ollama.exe"
    )
    if not Path(executable).is_file():
        raise RuntimeError("Installed Ollama is required for this optional local probe")
    fixture = ROOT / "qa-knowledge" / ("ollama-profiles-" + str(time.time_ns()))
    fixture.mkdir(parents=True)
    models = fixture / "models"
    blobs = models / "blobs"
    blobs.mkdir(parents=True)
    content = metadata_gguf()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    (blobs / digest.replace(":", "-")).write_bytes(content)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    env = dict(os.environ)
    env.update(
        OLLAMA_HOST=f"127.0.0.1:{port}",
        OLLAMA_MODELS=str(models),
        OLLAMA_NO_CLOUD="1",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    output = (fixture / "server.log").open("wb")
    process = subprocess.Popen(
        [executable, "serve"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        client = HermesModels(f"http://127.0.0.1:{port}")
        deadline = time.monotonic() + 30
        while not client.listing()["online"]:
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Isolated Ollama failed to start")
            time.sleep(0.2)
        with client.request(
            "create",
            {
                "model": "neutral:latest",
                "files": {"neutral.gguf": digest},
                "template": "{{if .Tools}}{{.Tools}}{{end}}{{.Prompt}}",
                "capabilities": ["tools"],
                "parameters": {"num_ctx": 8192},
                "stream": False,
            },
        ) as response:
            initial = json.load(response)
        assert initial.get("status") == "success", initial
        original = client.details("neutral:latest")
        result = client.prepare(
            fixture / "plugin",
            "neutral:latest",
            98304,
            threading.Event(),
            lambda message: None,
        )
        selected = client.details(result["model"])
        again = client.prepare(
            fixture / "plugin",
            "neutral:latest",
            98304,
            threading.Event(),
            lambda message: None,
        )
        with client.request("ps") as response:
            loaded = json.load(response).get("models", [])

        def weight_layers(name):
            path = (
                models / "manifests/registry.ollama.ai/library" / name.replace(":", "/")
            )
            data = json.loads(path.read_text())
            return [
                layer["digest"]
                for layer in data["layers"]
                if layer["mediaType"] == "application/vnd.ollama.image.model"
            ]

        report = {
            "passed": True,
            "real_ollama": True,
            "metadata_only_fixture": True,
            "context": configured_context(selected),
            "original_context": configured_context(client.details("neutral:latest")),
            "original_unchanged": client.details("neutral:latest") == original,
            "reused_on_repeat": again["model"] == result["model"],
            "same_weight_blobs": weight_layers("neutral:latest")
            == weight_layers(result["model"]),
            "loaded_models": len(loaded),
        }
        assert (
            report["context"] == 98304
            and report["original_context"] == 8192
            and report["original_unchanged"]
            and report["reused_on_repeat"]
            and report["same_weight_blobs"]
            and not loaded
        ), report
        (ROOT / "reports/v052-real-ollama.json").write_text(
            json.dumps(report, indent=2)
        )
        print(json.dumps(report), flush=True)
    finally:
        stop_owned(process)
        output.close()
        shutil.copyfile(
            fixture / "server.log", ROOT / "reports/v052-isolated-ollama.log"
        )
        if fixture.resolve().parent != ROOT / "qa-knowledge":
            raise RuntimeError("Unexpected fixture cleanup path")
        reject_links(fixture)
        shutil.rmtree(fixture)


if __name__ == "__main__":
    main()
