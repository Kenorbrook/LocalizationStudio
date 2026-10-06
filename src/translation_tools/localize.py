#!/usr/bin/env python3
"""Context-aware Ren'Py localization and review through a local Ollama model."""

from __future__ import annotations

import argparse
import collections
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from local_editor import (
    reference_examples,
    refusal_error,
    register_error,
    review_entries,
)

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
MIXED_ALPHABET_RE = re.compile(r"(?:[А-Яа-яЁё][A-Za-z]|[A-Za-z][А-Яа-яЁё])")
TOKEN_RE = re.compile(
    r"\[[^\[\]]+\]|\{[^{}]+\}|%\([^)]+\)[#0 +\-]*[a-zA-Z]|%[#0 +\-]*[a-zA-Z]|\\[nrt\"']"
)
NON_DIALOGUE_SPEAKERS = {"voice", "sound", "music"}
OLLAMA_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROMPT_VERSION = "2026-10-03-local-literary-v3"

SYSTEM_TRANSLATE = r"""
Ты ведущий литературный переводчик русской локализации взрослой визуальной новеллы.
Переведи поле english каждой записи с английского на естественный современный русский. Поля
context_before и context_after — соседние реплики только для понимания сцены: не объединяй их
с переводом и не пересказывай. Передавай действие, субъект, объект, эмоцию и степень грубости
точно; не добавляй новых фактов. Перестраивай английский порядок слов по нормам русского языка.

Карта персонажей, закреплённые имена, словарь и стиль проекта передаются ниже отдельным блоком
PROJECT RULES. Считай их обязательными. Пол говорящего определяй по speaker, пол адресата — по
смыслу реплики и соседнему контексту. Не переноси род говорящего на собеседника. Если роль не
описана, не выдумывай пол без контекстных оснований; по возможности перестрой фразу нейтрально.

Перед переводом каждого английского it/its/this/that обязательно найди конкретный референт в
текущей или соседней реплике, назови его по-русски и согласуй местоимение с родом именно этого
русского существительного: ключ — он/его, клетка — она/её, ожерелье — оно/его. Не переноси
английскую безродность в русский механическим «оно/его». Если местоимение звучит двусмысленно,
повтори существительное или перестрой фразу. Идиомы That's it / There it is обычно передавай
как «Вот так», «Именно», «Ну вот» или по смыслу сцены, а не буквальным «Вот оно».

Стиль: живой литературный русский без канцелярита и кальки. Реплики должны звучать так, будто
их изначально написали по-русски. 18+ лексику, ругань и интернет-сленг не смягчай и не
цензурируй, но и не усиливай относительно оригинала. Если исходный английский грамматически
сломанный, восстанови очевидный смысл по сцене, а не копируй ошибку в русский текст.

После чернового перевода мысленно прочитай каждую строку вслух и перепиши её, если носитель
русского так не сказал бы. Не сохраняй английское место наречий и обстоятельств, не перегружай
фразу причастиями и не вставляй слова вроде «один раз», если они не нужны для смысла. В авторском
тексте описывай движение плавно и наглядно, а не дословной конструкцией из словаря.

Особо следи за разговорными сокращениями и эротическим смыслом: ngl, lmao, mofo и похожие
выражения передавай уместным русским сленгом, а не оставляй непонятными латинскими вставками.
В повествовании сохраняй время оригинала. Если буквальный перевод звучит деревянно, выбери
естественную русскую фразу с тем же смыслом, субъектом и эмоциональной окраской.
Если в оригинале короткий стон или выкрик, не растягивай его в длинную цепочку букв: сохраняй
примерно ту же длину и эмоциональность. Не вставляй реальные переносы строк внутрь значения
text; если перенос нужен по смыслу, используй экранированный \n только когда он был в оригинале.

КРИТИЧЕСКИ ВАЖНО: сохрани каждый токен вида [name], {tag}, %(name)s, %s и обратные слеши
абсолютно без изменений: не переводи текст внутри квадратных или фигурных скобок и не меняй
регистр. Не добавляй пояснений. Верни только JSON-объект
{"items":[{"id":1,"text":"перевод"}, ...]} со всеми переданными id ровно по одному разу.
""".strip()

SYSTEM_REVIEW = r"""
Ты ведущий редактор русской локализации взрослой визуальной новеллы. Проверь каждый черновой
перевод рядом с английским оригиналом, соседними английскими репликами и уже переведённым
русским контекстом. Исправляй: неверный пол говорящего или адресата, согласование рода, потерю
смысла, буквальные кальки, выдуманные слова, деревянный порядок слов, неестественные обращения,
разнобой имён и терминологии. Карта персонажей, имена, словарь и стиль проекта находятся в
обязательном блоке PROJECT RULES ниже. 18+ лексику не цензурируй. Токены [name], {tag},
%(name)s, %s и экранирование сохраняй абсолютно точно.

Для каждого it/its/this/that найди референт в текущей или соседней реплике и проверь род его
русского названия: ключ — он, клетка — она, ожерелье — оно. Не оставляй механическое «оно»,
если русский референт мужского или женского рода. Убирай кальки «Вот оно» для That's it / There
it is: по контексту это обычно «Вот так», «Именно» или «Ну вот».

Верни КАЖДУЮ переданную строку. Если черновик уже точный и естественный, верни его без изменений:
не перефразируй ради самого перефразирования и не ухудшай хорошую строку. Меняй только то, для
чего можешь назвать конкретную причину по оригиналу, контексту или PROJECT RULES. Перед ответом
мысленно прочитай строку вслух и проверь, что субъект, объект, род и степень грубости сохранены.
Формат — только JSON со всеми id ровно по одному разу:
{"items":[{"id":1,"text":"проверенный перевод"}, ...]}.
""".strip()


def load_project_rules(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"project rules must be a JSON object: {path}")
    return value


def format_project_rules(rules: dict) -> str:
    if not rules:
        return "PROJECT RULES: специальных правил проекта нет; опирайся на speaker и контекст."

    lines = [f"PROJECT RULES — {rules.get('project', 'без названия')}:"]
    protagonist = rules.get("protagonist")
    if isinstance(protagonist, dict):
        details = ", ".join(f"{key}={value}" for key, value in protagonist.items())
        lines.append(f"- Главный герой: {details}.")

    speakers = rules.get("speakers", {})
    if isinstance(speakers, dict) and speakers:
        lines.append("- Персонажи (speaker -> обязательное описание):")
        for speaker, description in speakers.items():
            if isinstance(description, dict):
                rendered = ", ".join(
                    f"{key}={value}" for key, value in description.items()
                )
            else:
                rendered = str(description)
            lines.append(f"  - {speaker}: {rendered}")

    for heading, key in (("Закреплённые имена", "names"), ("Словарь", "glossary")):
        values = rules.get(key, {})
        if isinstance(values, dict) and values:
            lines.append(f"- {heading} (не создавать варианты):")
            for source, target in values.items():
                lines.append(f"  - {source} -> {target}")

    style_rules = rules.get("style_rules", [])
    if isinstance(style_rules, list) and style_rules:
        lines.append("- Дополнительные правила стиля:")
        lines.extend(f"  - {rule}" for rule in style_rules)

    forbidden = rules.get("forbidden_calques", {})
    if isinstance(forbidden, dict) and forbidden:
        lines.append("- Запрещённые кальки (используй указанные замены по контексту):")
        for bad, replacement in forbidden.items():
            lines.append(f"  - {bad} -> {replacement}")
    return "\n".join(lines)


def build_system_prompt(
    mode: str, rules: dict, profile: dict | None = None, entries: list | None = None
) -> str:
    base = (profile or {}).get(f"{mode}_prompt") or (
        SYSTEM_TRANSLATE if mode == "translate" else SYSTEM_REVIEW
    )
    retry_rule = (
        "Если payload содержит validation_feedback, исправь перечисленные технические ошибки "
        "и снова верни все находящиеся в items id. Не переводи и не меняй токены."
    )
    examples = (
        reference_examples(profile or {}, entries or [])
        if entries
        else (profile or {}).get("editorial_examples", [])
    )
    rendered = "\n".join(
        f"- {x['english']} -> {x['russian']} ({x['lesson']})" for x in examples
    )
    return (
        base
        + "\n\n"
        + format_project_rules(rules)
        + ("\n\nПримеры редактуры:\n" + rendered if rendered else "")
        + "\n\n"
        + retry_rule
    )


@functools.lru_cache(maxsize=20)
def source_scene_map(path: Path) -> dict[int, tuple]:
    if not path.is_file():
        return {}
    label = "start"
    scopes = []
    result = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if text.startswith("label "):
            label = text.split()[1].rstrip(":")
            scopes = []
        else:
            while scopes and scopes[-1][0] >= indent:
                scopes.pop()
            if text.endswith(":") and (
                re.match(r"(?:if|elif|else|menu)\b", text)
                or text.startswith(('"', "'"))
            ):
                scopes.append((indent, number))
        result[number] = (label, tuple(number for _, number in scopes))
    return result


def context_neighbors(
    records: list[dict], position: int, count: int, scene_boundaries: bool = False
) -> tuple[list[dict], list[dict]]:
    before = records[max(0, position - count) : position]
    after = records[position + 1 : position + 1 + count]
    if scene_boundaries:
        scene = records[position].get("scene")
        contiguous_before = []
        for neighbor in reversed(before):
            if neighbor.get("scene") != scene:
                break
            contiguous_before.append(neighbor)
        before = list(reversed(contiguous_before))
        contiguous_after = []
        for neighbor in after:
            if neighbor.get("scene") != scene:
                break
            contiguous_after.append(neighbor)
        after = contiguous_after
    return before, after


def decode_literal(literal: str) -> str:
    return json.loads(literal)


def quoted_span(line: str) -> tuple[int, int] | None:
    start = line.find('"')
    if start < 0:
        return None
    escaped = False
    for i in range(start + 1, len(line)):
        ch = line[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            return start, i + 1
    return None


def parse_literal(line: str) -> tuple[str, str, str] | None:
    span = quoted_span(line)
    if span is None:
        return None
    start, end = span
    try:
        value = decode_literal(line[start:end])
    except json.JSONDecodeError:
        return None
    return line[:start], value, line[end:]


def speaker_from_prefix(prefix: str) -> str:
    cleaned = prefix.strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    return cleaned.split()[-1] if cleaned else "n"


def find_records(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    records: list[dict] = []
    pending_comments: list[tuple[int, str, str]] = []
    pending_old: tuple[int, str] | None = None
    scene = ("interface", ())
    source_line = None

    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        parsed = parse_literal(line)
        stripped = line.lstrip()

        reference = re.match(r"# (game/[^:]+):(\d+)\s*$", stripped)
        if reference:
            source_line = int(reference.group(2))
            game_directory = next(
                (parent for parent in path.parents if parent.name == "game"), None
            )
            source_path = (
                game_directory.parent if game_directory else path.parents[3]
            ) / reference.group(1)
            scene = source_scene_map(source_path).get(
                source_line, (reference.group(1), ())
            )

        if stripped.startswith("translate "):
            pending_comments = []
            pending_old = None
            continue

        if stripped.startswith("#") and parsed:
            prefix, source, _ = parsed
            pending_comments.append((index, source, speaker_from_prefix(prefix)))
            continue

        if parsed and stripped.startswith("old "):
            pending_old = (index, parsed[1])
            continue

        if not parsed or stripped.startswith("#"):
            continue

        prefix, current, suffix = parsed
        source = None
        speaker = speaker_from_prefix(prefix)

        if stripped.startswith("new ") and pending_old:
            source = pending_old[1]
            speaker = "interface"
            pending_old = None
        elif pending_comments:
            _, source, speaker = pending_comments.pop(0)

        if source is None or speaker in NON_DIALOGUE_SPEAKERS:
            continue

        newline = (
            "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
        )
        records.append(
            {
                "line": index,
                "source": source,
                "current": current,
                "speaker": speaker,
                "prefix": prefix,
                "suffix": suffix,
                "newline": newline,
                "scene": scene,
                "source_line": source_line,
            }
        )

    return records


def stable_key(relative: str, record: dict, mode: str, model: str) -> str:
    material = (
        record["source"]
        if mode == "translate"
        else record["source"] + "\0" + record["current"]
    )
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"{mode}:{model}:{relative}:{record['line'] + 1}:{digest}"


def tokens(text: str) -> collections.Counter:
    return collections.Counter(TOKEN_RE.findall(text))


def validate(source: str, translated: str) -> str | None:
    if not source.strip():
        return None if not translated.strip() else "unexpected text for empty source"
    if not translated.strip():
        return "empty translation"
    if len(translated) > max(600, len(source) * 4 + 120):
        return f"translation too long: {len(translated)} chars for {len(source)} source chars"
    if tokens(source) != tokens(translated):
        return f"token mismatch: {dict(tokens(source))} != {dict(tokens(translated))}"
    return None


def validate_quality(
    source: str, translated: str, rules: dict | None = None
) -> str | None:
    error = validate(source, translated)
    if error:
        return error
    error = refusal_error(source, translated)
    if error:
        return error
    error = register_error(source, translated)
    if error:
        return error
    if "\ufffd" in translated:
        return "invalid replacement character"
    cleaned = translated.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    if MIXED_ALPHABET_RE.search(cleaned):
        return "mixed Cyrillic/Latin word"
    forbidden_output = (rules or {}).get("forbidden_output", [])
    if isinstance(forbidden_output, list):
        lowered = translated.casefold()
        for phrase in forbidden_output:
            if isinstance(phrase, str) and phrase.casefold() in lowered:
                return f"forbidden output fragment: {phrase!r}"
    return None


def ollama_chat(
    model: str,
    system: str,
    payload: dict,
    timeout: int,
    think: bool,
    num_ctx: int = 16384,
    keep_alive: str = "5m",
    options: dict | None = None,
) -> dict:
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "format": "json",
            "think": think,
            "keep_alive": keep_alive,
            "options": {"temperature": 0.08, "num_ctx": num_ctx, **(options or {})},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with OLLAMA_OPENER.open(request, timeout=timeout) as response:
        outer = json.loads(response.read().decode("utf-8"))
    if outer.get("done_reason") == "length":
        raise ValueError(
            "Ollama response was truncated; reduce chunk size or increase context"
        )
    content = outer["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        repaired = content.replace('\\"', '"')
        items_at = repaired.find('"items"')
        start = repaired.rfind("{", 0, items_at) if items_at >= 0 else -1
        if start >= 0:
            try:
                return json.JSONDecoder().raw_decode(repaired[start:])[0]
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Ollama returned invalid JSON: {content[:2000]}") from error


def chunks(records: list[dict], max_items: int, max_chars: int):
    current: list[dict] = []
    size = 0
    for record in records:
        record_size = len(record["source"]) + len(record["current"]) + 80
        if current and (len(current) >= max_items or size + record_size > max_chars):
            yield current
            current = []
            size = 0
        current.append(record)
        size += record_size
    if current:
        yield current


def load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def update_file(
    path: Path, replacements: dict[int, str], records_by_line: dict[int, dict]
) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    for line_number, translated in replacements.items():
        record = records_by_line[line_number]
        literal = json.dumps(translated, ensure_ascii=False)
        lines[line_number] = (
            record["prefix"] + literal + record["suffix"] + record["newline"]
        )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8", newline="")
    os.replace(temporary, path)


def process_file(
    path: Path,
    tl_root: Path,
    cache: dict[str, str],
    cache_path: Path,
    args: argparse.Namespace,
    progress: dict | None = None,
) -> tuple[int, int]:
    relative = path.relative_to(tl_root).as_posix()
    records = find_records(path)
    records_by_line = {record["line"]: record for record in records}
    record_positions = {record["line"]: index for index, record in enumerate(records)}
    mode = args.mode
    candidates: list[dict] = []

    for record in records:
        key = stable_key(relative, record, mode, args.cache_signature)
        record["key"] = key
        if not record["source"].strip():
            cache[key] = record["current"]
            continue
        source_tokens = TOKEN_RE.findall(record["source"])
        if (
            mode == "translate"
            and len(source_tokens) == 1
            and source_tokens[0] == record["source"].strip()
        ):
            cache[key] = record["current"]
            continue
        if key in cache:
            if cache[key] != record["current"]:
                record["cached"] = cache[key]
                candidates.append(record)
            continue
        if mode == "translate":
            if CYRILLIC_RE.search(record["current"]) and not args.force:
                cache[key] = record["current"]
            else:
                candidates.append(record)
        else:
            if CYRILLIC_RE.search(record["current"]):
                candidates.append(record)

    if not candidates:
        return len(records), 0

    if progress is not None and args.limit is not None:
        remaining_limit = max(progress["total"] - progress["attempted"], 0)
        candidates = candidates[:remaining_limit]
        if not candidates:
            return len(records), 0

    changed = 0
    all_replacements: dict[int, str] = {}
    batches = list(chunks(candidates, args.chunk_items, args.chunk_chars))
    print(f"{relative}: {len(candidates)} items in {len(batches)} batches", flush=True)

    for batch_number, batch in enumerate(batches, 1):
        replacements: dict[int, str] = {}
        uncached = [record for record in batch if "cached" not in record]
        for record in batch:
            if "cached" in record:
                replacements[record["line"]] = record["cached"]

        if uncached:
            entries = []
            for item_id, record in enumerate(uncached, 1):
                entry = {
                    "id": item_id,
                    "speaker": record["speaker"],
                    "english": record["source"],
                }
                position = record_positions[record["line"]]
                before, after = context_neighbors(
                    records,
                    position,
                    args.context_lines,
                    args.profile_data.get("scene_boundaries", False),
                )
                if args.profile_data:
                    entry["scene"] = record["scene"][0]
                    entry["speaker_role"] = args.project_rules.get("speakers", {}).get(
                        record["speaker"], {}
                    )

                def context_entry(neighbor: dict) -> dict:
                    value = {
                        "speaker": neighbor["speaker"],
                        "english": neighbor["source"],
                    }
                    if args.profile_data.get(
                        "include_russian_context", True
                    ) and CYRILLIC_RE.search(neighbor["current"]):
                        value["russian"] = neighbor["current"]
                    return value

                entry["context_before"] = [
                    context_entry(neighbor) for neighbor in before
                ]
                entry["context_after"] = [context_entry(neighbor) for neighbor in after]
                if mode == "review":
                    entry["russian"] = record["current"]
                entries.append(entry)

            entries_by_id = {entry["id"]: entry for entry in entries}
            remaining_ids = set(entries_by_id)
            accepted: dict[int, str] = {}
            feedback: dict[int, str] = {}
            last_error = None
            successful_response = False

            for attempt in range(1, args.retries + 1):
                retry_entries = [
                    entries_by_id[item_id] for item_id in sorted(remaining_ids)
                ]
                payload = {"file": relative, "mode": mode, "items": retry_entries}
                if feedback:
                    payload["validation_feedback"] = [
                        {"id": item_id, "error": feedback[item_id]}
                        for item_id in sorted(feedback)
                        if item_id in remaining_ids
                    ]
                try:
                    print(
                        f"  batch {batch_number}/{len(batches)} request attempt {attempt}/{args.retries} "
                        f"items={len(retry_entries)}",
                        flush=True,
                    )

                    def local_call(stage, stage_entries):
                        stage_payload = {
                            **payload,
                            "mode": stage,
                            "items": stage_entries,
                        }
                        return ollama_chat(
                            args.model,
                            build_system_prompt(
                                stage,
                                args.project_rules,
                                args.profile_data,
                                stage_entries,
                            ),
                            stage_payload,
                            args.timeout,
                            args.think,
                            args.num_ctx,
                            args.keep_alive,
                            {
                                "temperature": args.temperature,
                                **(
                                    {"seed": args.seed} if args.seed is not None else {}
                                ),
                                **args.profile_data.get("sampling_options", {}),
                            },
                        )

                    def trace_review(event):
                        args.review_pending = getattr(args, "review_pending", 0) + (
                            event["status"] in {"uncertain", "rejected"}
                        )
                        journal = (
                            args.root
                            / "translation_tools"
                            / f"decisions-{args.profile_data['name']}.jsonl"
                        )
                        journal.parent.mkdir(parents=True, exist_ok=True)
                        record = uncached[event["id"] - 1]
                        with journal.open("a", encoding="utf-8") as output:
                            output.write(
                                json.dumps(
                                    {
                                        "file": relative,
                                        "line": record["line"] + 1,
                                        **event,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )

                    if (
                        mode == "review"
                        and args.profile_data.get("review_strategy")
                        == "critic-rewrite-verify"
                    ):
                        response = review_entries(
                            retry_entries,
                            local_call,
                            lambda source, text: validate_quality(
                                source, text, args.project_rules
                            ),
                            trace_review,
                        )
                    else:
                        response = local_call(mode, retry_entries)
                    successful_response = True
                except (
                    OSError,
                    urllib.error.URLError,
                    json.JSONDecodeError,
                    KeyError,
                    ValueError,
                ) as error:
                    last_error = error
                    print(
                        f"  batch {batch_number}: retry {attempt}/{args.retries}: {error}",
                        flush=True,
                    )
                    time.sleep(min(attempt * 2, 8))
                    continue

                returned = response.get("items", [])
                by_id = {
                    item.get("id"): item.get("text")
                    for item in returned
                    if isinstance(item, dict)
                }
                id_counts = collections.Counter(
                    item.get("id") for item in returned if isinstance(item, dict)
                )
                reasons = {
                    item.get("id"): item.get("reason", "")
                    for item in returned
                    if isinstance(item, dict)
                }
                next_remaining: set[int] = set()
                next_feedback: dict[int, str] = {}
                for item_id in sorted(remaining_ids):
                    translated = by_id.get(item_id)
                    if not isinstance(translated, str) or id_counts[item_id] != 1:
                        next_remaining.add(item_id)
                        next_feedback[item_id] = "missing id or text is not a string"
                        continue
                    record = uncached[item_id - 1]
                    error = validate_quality(
                        record["source"], translated, args.project_rules
                    )
                    if (
                        mode == "review"
                        and args.profile_data.get("require_edit_reason")
                        and translated != record["current"]
                        and not str(reasons.get(item_id, "")).strip()
                    ):
                        error = "changed translation without a specific edit reason"
                    if error:
                        next_remaining.add(item_id)
                        next_feedback[item_id] = error
                        print(
                            f"  rejected {relative}:{record['line'] + 1}: {error}",
                            flush=True,
                        )
                        continue
                    accepted[item_id] = translated
                    if (
                        mode == "review"
                        and args.profile_data
                        and translated != record["current"]
                    ):
                        journal = (
                            args.root
                            / "translation_tools"
                            / f"editorial-{args.profile_data['name']}.jsonl"
                        )
                        with journal.open("a", encoding="utf-8") as output:
                            output.write(
                                json.dumps(
                                    {
                                        "file": relative,
                                        "line": record["line"] + 1,
                                        "source": record["source"],
                                        "before": record["current"],
                                        "after": translated,
                                        "reason": reasons.get(item_id, ""),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )

                remaining_ids = next_remaining
                feedback = next_feedback
                if not remaining_ids:
                    break
                if attempt < args.retries:
                    print(
                        f"  batch {batch_number}: retrying invalid ids {sorted(remaining_ids)}",
                        flush=True,
                    )
                    time.sleep(min(attempt * 2, 8))

            if not successful_response:
                if isinstance(last_error, (OSError, urllib.error.URLError)):
                    raise RuntimeError(
                        f"Ollama connection failed for {relative} batch {batch_number}: {last_error}"
                    ) from last_error
                print(
                    f"  batch {batch_number}: skipping after {args.retries} malformed responses: "
                    f"{last_error}",
                    flush=True,
                )

            for item_id, translated in accepted.items():
                record = uncached[item_id - 1]
                replacements[record["line"]] = translated
                cache[record["key"]] = translated
                if args.profile_data and mode == "review":
                    updated_record = {**record, "current": translated}
                    cache[
                        stable_key(relative, updated_record, mode, args.cache_signature)
                    ] = translated

            for item_id in sorted(remaining_ids):
                record = uncached[item_id - 1]
                cache.pop(record["key"], None)
                args.unresolved = getattr(args, "unresolved", 0) + 1
                print(
                    f"  skipped after {args.retries} attempts {relative}:{record['line'] + 1}: "
                    f"{feedback.get(item_id, 'no valid response')}",
                    flush=True,
                )

        for record in batch:
            translated = replacements.get(record["line"], record["current"])
            if translated != record["current"]:
                changed += 1
                all_replacements[record["line"]] = translated

        if all_replacements:
            update_file(path, all_replacements, records_by_line)
        save_json_atomic(cache_path, cache)
        print(f"  batch {batch_number}/{len(batches)} complete", flush=True)
        if progress is not None:
            progress["attempted"] += len(batch)
            progress["accepted"] += len(replacements)
            elapsed = max(time.monotonic() - progress["started"], 0.001)
            rate = progress["attempted"] / elapsed
            remaining = max(progress["total"] - progress["attempted"], 0)
            eta_minutes = remaining / max(rate, 1e-9) / 60
            percent = 100 * progress["attempted"] / max(progress["total"], 1)
            print(
                f"  OVERALL {progress['attempted']}/{progress['total']} ({percent:.1f}%) "
                f"accepted={progress['accepted']} rate={rate:.2f} lines/s "
                f"ETA={eta_minutes:.1f} min",
                flush=True,
            )

    return len(records), changed


def run_static_qa(files: list[Path], rules: dict) -> int:
    total = 0
    errors: list[tuple[Path, int, str]] = []
    for path in files:
        for record in find_records(path):
            total += 1
            error = validate_quality(record["source"], record["current"], rules)
            if error:
                errors.append((path, record["line"] + 1, error))
    print(f"QA files={len(files)} records={total} errors={len(errors)}", flush=True)
    for path, line, error in errors:
        print(f"  {path}:{line}: {error}", flush=True)
    return 1 if errors else 0


def run_model_mode(
    mode: str,
    files: list[Path],
    tl_root: Path,
    args: argparse.Namespace,
) -> tuple[int, int]:
    args.mode = mode
    namespace = f".{args.profile_data['name']}" if args.profile_data else ""
    cache_path = args.root / "translation_tools" / f".{mode}-cache{namespace}.json"
    cache = load_cache(cache_path)
    pending = 0
    for path in files:
        relative = path.relative_to(tl_root).as_posix()
        for record in find_records(path):
            key = stable_key(relative, record, mode, args.cache_signature)
            if not record["source"].strip():
                continue
            source_tokens = TOKEN_RE.findall(record["source"])
            if (
                mode == "translate"
                and len(source_tokens) == 1
                and source_tokens[0] == record["source"].strip()
            ):
                continue
            if key in cache and cache[key] == record["current"]:
                continue
            if mode == "translate":
                if CYRILLIC_RE.search(record["current"]) and not args.force:
                    continue
                pending += 1
            elif CYRILLIC_RE.search(record["current"]):
                pending += 1

    if args.limit is not None:
        pending = min(pending, args.limit)
    progress = {
        "total": pending,
        "attempted": 0,
        "accepted": 0,
        "started": time.monotonic(),
    }
    print(
        f"START mode={mode} model={args.model} files={len(files)} pending={pending} "
        f"num_ctx={args.num_ctx} context_lines={args.context_lines}",
        flush=True,
    )
    total = 0
    changed = 0
    for path in files:
        file_total, file_changed = process_file(
            path, tl_root, cache, cache_path, args, progress
        )
        total += file_total
        changed += file_changed
    print(
        f"DONE mode={mode} files={len(files)} records={total} changed={changed}",
        flush=True,
    )
    return total, changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Context-aware Ren'Py translation, editorial review, and static QA."
    )
    parser.add_argument("mode", choices=("translate", "review", "pipeline", "qa"))
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Game root; must contain game/tl/russian",
    )
    parser.add_argument("--model", default="qwen3.8:latest")
    parser.add_argument("--config", type=Path, help="Project-specific JSON rules")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(__file__).with_name("local_literary_profile.json"),
        help="Shared default profile; separate caches",
    )
    parser.add_argument("--temperature", type=float, default=0.08)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--include", action="append", help="Glob relative to game/tl/russian"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum pending records per model stage (control runs)",
    )
    parser.add_argument("--chunk-items", type=int, default=4)
    parser.add_argument("--chunk-chars", type=int, default=2800)
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument(
        "--keep-alive", default="5m", help="How long Ollama keeps the model loaded"
    )
    parser.add_argument(
        "--force", action="store_true", help="Translate existing Russian records again"
    )
    parser.add_argument(
        "--think", action="store_true", help="Enable model reasoning when supported"
    )
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.profile_data = load_project_rules(args.profile)
    if args.profile is not None and not args.profile.is_file():
        parser.error(f"profile not found: {args.profile}")
    if args.profile_data:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.profile_data.get("name", "")):
            parser.error(
                "profile name must contain only letters, digits, underscore or hyphen"
            )
        for name, value in args.profile_data.get("parameters", {}).items():
            if name not in {
                "num_ctx",
                "context_lines",
                "chunk_items",
                "chunk_chars",
                "temperature",
                "seed",
            }:
                parser.error(f"unknown profile parameter: {name}")
            flag = f"--{name.replace('_', '-')}"
            if not any(
                argument == flag or argument.startswith(flag + "=")
                for argument in sys.argv
            ):
                setattr(args, name, value)

    if args.chunk_items < 1 or args.chunk_chars < 200 or args.context_lines < 0:
        parser.error(
            "chunk-items must be >= 1, chunk-chars >= 200, and context-lines >= 0"
        )
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be >= 1")

    tl_root = args.root / "game" / "tl" / "russian"
    if not tl_root.is_dir():
        parser.error(f"translation directory not found: {tl_root}")

    config_path = args.config or (
        args.root / "translation_tools" / "localization_config.json"
    )
    if args.config is not None and not config_path.is_file():
        parser.error(f"project rules not found: {config_path}")
    try:
        args.project_rules = load_project_rules(
            config_path if config_path.is_file() else None
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    rules_material = json.dumps(args.project_rules, ensure_ascii=False, sort_keys=True)
    rules_digest = hashlib.sha1(rules_material.encode("utf-8")).hexdigest()[:10]
    args.cache_signature = f"{args.model}:{PROMPT_VERSION}:{rules_digest}"
    if args.profile_data:
        material = {
            "profile": args.profile_data,
            "rules": args.project_rules,
            "settings": {
                key: getattr(args, key)
                for key in (
                    "num_ctx",
                    "context_lines",
                    "chunk_items",
                    "chunk_chars",
                    "temperature",
                    "seed",
                    "think",
                )
            },
        }
        material["engine_hashes"] = {
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in ("localize.py", "local_editor.py")
        }
        request = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        if args.mode != "qa":
            with OLLAMA_OPENER.open(request, timeout=10) as response:
                tags = json.load(response).get("models", [])
            material["model_digest"] = next(
                (x.get("digest") for x in tags if x.get("name") == args.model),
                args.model,
            )
        args.cache_signature += (
            ":"
            + hashlib.sha256(
                json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16]
        )

    patterns = args.include or ["**/*.rpy"]
    files = sorted(
        {
            path
            for pattern in patterns
            for path in tl_root.glob(pattern)
            if path.is_file()
        }
    )
    print(
        f"CONFIG path={config_path if config_path.is_file() else 'none'} "
        f"prompt={PROMPT_VERSION} rules={rules_digest} "
        f"profile={args.profile_data.get('name', 'legacy')} model={args.model} context={args.num_ctx}",
        flush=True,
    )

    if args.mode == "qa":
        return run_static_qa(files, args.project_rules)

    requested_mode = args.mode
    args.unresolved = 0
    args.review_pending = 0
    modes = (
        ("translate", "review") if requested_mode == "pipeline" else (requested_mode,)
    )
    for mode in modes:
        run_model_mode(mode, files, tl_root, args)
    print(
        f"LOCAL REVIEW pending={args.review_pending}; inspect decisions journal for uncertain/rejected edits",
        flush=True,
    )

    if args.profile_data and args.unresolved:
        print(
            f"INCOMPLETE unresolved={args.unresolved}; rerun to retry skipped records",
            flush=True,
        )
        return 2

    if requested_mode == "pipeline":
        return run_static_qa(files, args.project_rules)
    return 0


if __name__ == "__main__":
    sys.exit(main())
