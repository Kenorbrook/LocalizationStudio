"""No external requests or credentials: validate the optional cloud API contract."""

import tempfile, sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from providers import Provider, process_record, ServiceUnavailable

calls = []


def request(url, data=None, headers=None, local=False):
    calls.append((url, data))
    assert url == "https://provider.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer TEST-ONLY-NOT-A-KEY"
    assert data["response_format"] == {"type": "json_object"}
    assert data["max_tokens"] == 500
    return {
        "choices": [
            {"message": {"content": '{"items":[{"id":1,"text":"Дверь закрыта."}]}'}}
        ]
    }


with tempfile.TemporaryDirectory() as directory:
    with (
        patch("providers.secret", return_value="TEST-ONLY-NOT-A-KEY"),
        patch("providers.json_request", side_effect=request),
    ):
        provider = Provider(
            {"id": -1, "root": directory},
            "cloud",
            {
                "endpoint": "https://provider.example/v1",
                "cloud_model": "test",
                "max_calls": 1,
                "max_output": 500,
            },
        )
        value, _ = process_record(
            provider, "translate", {"id": 1, "english": "The door is closed."}
        )
        assert value == "Дверь закрыта."
        try:
            provider.call("translate", [{"id": 1, "english": "Hi"}])
        except ServiceUnavailable:
            pass
        else:
            raise AssertionError("Request limit not enforced")
print(
    "Cloud adapter contract: PASS; external requests: 0; requests simulated:",
    len(calls),
)
