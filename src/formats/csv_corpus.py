"""csv_corpus source adapter."""

import csv


def parse(path, text):
    suffix = path.suffix.lower()
    rows = list(csv.DictReader(text.splitlines()))
    if not rows or "source" not in rows[0]:
        raise ValueError(
            "CSV должен содержать столбец source; translation необязателен"
        )
    return (
        [
            dict(
                source=r["source"],
                text=r.get("translation", ""),
                speaker=r.get("speaker")
                or r.get("speaker_id")
                or r.get("character")
                or r.get("actor")
                or "",
                scene=r.get("scene", ""),
                locator={
                    "row": i,
                    "utterance_kind": r.get("utterance_kind") or r.get("line_type"),
                    "preserve_original": str(r.get("preserve_original", "")).lower()
                    in {"true", "1", "yes"},
                    "preserve_reason": r.get("preserve_reason"),
                    "original_language": r.get("original_language"),
                },
            )
            for i, r in enumerate(rows)
        ],
        text,
        "csv",
    )
