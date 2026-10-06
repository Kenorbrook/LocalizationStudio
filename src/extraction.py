"""Read source files and dispatch to explicit offline format adapters."""

from formats import renpy, json_corpus, csv_corpus, plain_text
from formats.literals import literal, SPAN

ADAPTERS = {
    ".rpy": renpy.parse,
    ".json": json_corpus.parse,
    ".jsonl": json_corpus.parse,
    ".csv": csv_corpus.parse,
    ".txt": plain_text.parse,
}


def extract(path, text=None):
    adapter = ADAPTERS.get(path.suffix.lower())
    if adapter is None:
        raise ValueError("Поддерживаются .rpy, .txt, .json, .jsonl и .csv")
    if text is None:
        text = path.read_text(encoding="utf-8-sig")
    return adapter(path, text)
