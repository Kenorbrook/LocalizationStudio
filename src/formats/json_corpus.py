"""json_corpus source adapter."""

import json


def parse(path, text):
    suffix = path.suffix.lower()
    data = (
        json.loads(text)
        if suffix == ".json"
        else [json.loads(l) for l in text.splitlines() if l.strip()]
    )
    records = []

    def walk(x, loc):
        if isinstance(x, dict) and isinstance(x.get("source"), str):
            records.append(
                dict(
                    source=x["source"],
                    text=x.get("translation", ""),
                    speaker=str(
                        x.get("speaker")
                        or x.get("speaker_id")
                        or x.get("character")
                        or x.get("actor")
                        or ""
                    ),
                    scene=str(x.get("scene", "")),
                    locator={
                        "path": loc,
                        "field": "translation",
                        "utterance_kind": x.get("utterance_kind") or x.get("line_type"),
                        "preserve_original": x.get("preserve_original") is True,
                        "preserve_reason": x.get("preserve_reason"),
                        "original_language": x.get("original_language"),
                    },
                )
            )
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, loc + [k])
        elif isinstance(x, list):
            for k, v in enumerate(x):
                walk(v, loc + [k])
        elif isinstance(x, str):
            records.append(
                dict(
                    source=x,
                    text="",
                    speaker="",
                    scene="",
                    locator={"path": loc, "replace": True},
                )
            )

    walk(data, [])
    return records, text, "jsonl" if suffix == ".jsonl" else "json"
