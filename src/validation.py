"""Translation invariants shared by editing and export."""

import collections, re
from local_editor import refusal_error

TOKEN = re.compile(
    r"\[[^\[\]\n]+\]|\{[^{}\n]+\}|%\([^)]+\)[#0+\-\d.]*[sdif]|%(?:[#0+\-\d.]*[sdif])(?=\W|$)|\\[nrt]"
)


def validate(source, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Пустой перевод")
    if collections.Counter(TOKEN.findall(source)) != collections.Counter(
        TOKEN.findall(text)
    ):
        raise ValueError("Не совпадают теги или переменные")
    if any(source.count(c) != text.count(c) for c in "\n\r\t"):
        raise ValueError("Не совпадают переносы строк или табуляция")
    error = refusal_error(source, text)
    if error:
        raise ValueError(error)
