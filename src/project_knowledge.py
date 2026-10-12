"""Evidence-backed project memory, human decisions and bounded retrieval."""

import hashlib
import json
import re
from contextlib import contextmanager
from core import dump, now


def source_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def live_clause():
    return """EXISTS(SELECT 1 FROM knowledge_evidence e WHERE e.item=k.id)
    AND NOT EXISTS(SELECT 1 FROM knowledge_evidence e LEFT JOIN records r ON r.id=e.record
    LEFT JOIN files f ON f.id=r.file WHERE e.item=k.id AND
    (r.id IS NULL OR f.project<>k.project OR e.source_hash<>source_digest(r.source)))"""


@contextmanager
def connect(store):
    with store.db() as connection:
        connection.create_function(
            "source_digest", 1, lambda text: source_hash(text or "")
        )
        yield connection


def validate_facts(result, records):
    if not isinstance(result, dict) or not isinstance(result.get("facts"), list):
        raise ValueError("Анализ должен вернуть объект facts с массивом сведений")
    if len(result["facts"]) > 40:
        raise ValueError("Слишком много сведений в одном ответе; максимум 40")
    known = {r["id"]: r for r in records}
    facts = []
    for raw in result["facts"]:
        if not isinstance(raw, dict) or raw.get("kind") not in {
            "character",
            "relationship",
            "term",
            "scene",
        }:
            raise ValueError("Некорректный вид сведений")
        item = {"kind": raw["kind"]}
        for field in ("subject", "attribute", "value", "reason"):
            value = raw.get(field, "")
            if not isinstance(value, str) or not value.strip() or len(value) > 800:
                raise ValueError("Некорректное поле анализа: " + field)
            item[field] = value.strip()
        confidence = raw.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Некорректная уверенность анализа")
        if not isinstance(raw.get("uncertain"), bool):
            raise ValueError("Анализ должен явно указать uncertain")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
            raise ValueError("Для каждого сведения нужны исходные реплики")
        item["evidence"] = []
        for ref in evidence:
            if not isinstance(ref, dict):
                raise ValueError("Некорректная ссылка на оригинал")
            record = known.get(ref.get("id")) if type(ref.get("id")) is int else None
            quote = ref.get("quote")
            if (
                not record
                or not isinstance(quote, str)
                or not quote.strip()
                or quote not in record["source"]
                or len(quote) > 1000
            ):
                raise ValueError("Цитата анализа не найдена в исходной реплике")
            item["evidence"].append({"id": record["id"], "quote": quote})
        mentions = raw.get("mentions", [])
        if (
            not isinstance(mentions, list)
            or len(mentions) > 12
            or any(not isinstance(x, str) or not 1 <= len(x) <= 100 for x in mentions)
        ):
            raise ValueError("Некорректные имена или термины для поиска")
        item.update(
            mentions=mentions, confidence=confidence, uncertain=raw["uncertain"]
        )
        # Scene summaries must never become rules for another branch or scene.
        first = known[item["evidence"][0]["id"]]
        item["file"] = first["file"]
        item["scene"] = first["scene"]
        item["state"] = "review" if raw["uncertain"] or confidence < 0.85 else "auto"
        facts.append(item)
    return facts


def save_batch(store, pid, key, facts, records, job=None):
    hashes = {r["id"]: source_hash(r["source"]) for r in records}
    with connect(store) as db:
        db.execute("BEGIN IMMEDIATE")
        if (
            job is not None
            and db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0]
            != "running"
        ):
            return False
        if db.execute(
            "SELECT 1 FROM knowledge_batches WHERE key=? AND project=?", (key, pid)
        ).fetchone():
            return True
        # Reject a response if the imported source changed while the model was working.
        for r in records:
            current = db.execute(
                "SELECT r.source,f.project FROM records r JOIN files f ON f.id=r.file WHERE r.id=?",
                (r["id"],),
            ).fetchone()
            if (
                not current
                or current["project"] != pid
                or source_hash(current["source"]) != hashes[r["id"]]
            ):
                raise ValueError(
                    "Исходник изменился во время анализа; повторите анализ"
                )
        for item in facts:
            previous = db.execute(
                "SELECT k.id,k.value,k.state,k.data FROM knowledge_items k WHERE project=? AND kind=? AND subject=? AND attribute=? AND ("
                + live_clause()
                + ")",
                (pid, item["kind"], item["subject"], item["attribute"]),
            ).fetchall()
            if item["kind"] == "scene":
                previous = [
                    r
                    for r in previous
                    if (json.loads(r["data"])["file"], json.loads(r["data"])["scene"])
                    == (item["file"], item["scene"])
                ]
            conflicts = [
                r
                for r in previous
                if r["value"] != item["value"] and r["state"] != "rejected"
            ]
            if conflicts and item["kind"] != "scene":
                item = {
                    **item,
                    "state": "review",
                    "reason": "Противоречие с другим сведением. " + item["reason"],
                }
                db.executemany(
                    "UPDATE knowledge_items SET state='review',revision=revision+1 WHERE id=? AND state='auto'",
                    [(r["id"],) for r in conflicts],
                )
            if any(r["value"] == item["value"] for r in previous):
                continue
            cursor = db.execute(
                "INSERT INTO knowledge_items(project,batch,kind,subject,attribute,value,data,state,at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    key,
                    item["kind"],
                    item["subject"],
                    item["attribute"],
                    item["value"],
                    dump(item),
                    item["state"],
                    now(),
                ),
            )
            db.executemany(
                "INSERT INTO knowledge_evidence VALUES (?,?,?)",
                [
                    (cursor.lastrowid, rid, hashes[rid])
                    for rid in {ref["id"] for ref in item["evidence"]}
                ],
            )
        db.execute(
            "INSERT INTO knowledge_batches VALUES (?,?,?,?)",
            (key, pid, dump(facts), now()),
        )
    return True


def page(store, pid, offset=0, state=""):
    offset = max(0, int(offset))
    where = "k.project=?"
    args = [pid]
    if state:
        if state not in {"auto", "review", "confirmed", "rejected"}:
            raise ValueError("Некорректный фильтр справочника")
        where += " AND k.state=?"
        args.append(state)
    with connect(store) as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT k.*, ("
                + live_clause()
                + ") live FROM knowledge_items k WHERE "
                + where
                + " ORDER BY CASE k.state WHEN 'review' THEN 0 ELSE 1 END,k.id DESC LIMIT 50 OFFSET ?",
                args + [offset],
            )
        ]
        total = db.execute(
            "SELECT count(*) FROM knowledge_items k WHERE " + where, args
        ).fetchone()[0]
        counts = dict(
            db.execute(
                "SELECT state,count(*) FROM knowledge_items WHERE project=? GROUP BY state",
                (pid,),
            ).fetchall()
        )
    for row in rows:
        row["data"] = json.loads(row["data"])
    return {"rows": rows, "total": total, "offset": offset, "counts": counts}


def decide(store, pid, iid, revision, action, value=None):
    if action not in {"confirmed", "rejected", "review"}:
        raise ValueError("Некорректное действие")
    with connect(store) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT k.*, ("
            + live_clause()
            + ") live FROM knowledge_items k WHERE id=? AND project=?",
            (iid, pid),
        ).fetchone()
        if not row or row["revision"] != revision:
            raise ValueError("Сведение изменилось; обновите список")
        if action == "confirmed" and not row["live"]:
            raise ValueError("Исходные реплики изменились; сначала повторите анализ")
        if value is None:
            value = row["value"]
        if not isinstance(value, str) or not value.strip() or len(value) > 800:
            raise ValueError("Текст сведения должен содержать от 1 до 800 символов")
        data = json.loads(row["data"])
        data["value"] = value.strip()
        db.execute(
            "UPDATE knowledge_items SET value=?,data=?,state=?,revision=revision+1 WHERE id=?",
            (value.strip(), dump(data), action, iid),
        )
    return {"ok": True}


def mentioned(term, source):
    if len(term) < 2:
        return False
    if re.search(r"[A-Za-zА-Яа-яЁё]", term):
        return bool(re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)", source))
    return term.lower() in source


def select_context(store, record, neighbors, settings):
    if settings.get("use_project_knowledge", True) is False:
        return {}
    source = "\n".join(r["source"] for r in neighbors).lower()
    speakers = {r.get("speaker", "") for r in neighbors} - {""}
    with connect(store) as db:
        rows = db.execute(
            "SELECT k.* FROM knowledge_items k WHERE k.project=? AND k.state<>'rejected' AND ("
            + live_clause()
            + ") AND (instr(lower(?),lower(k.subject))>0 OR k.subject IN (SELECT value FROM json_each(?)) OR EXISTS(SELECT 1 FROM json_each(k.data,'$.mentions') m WHERE length(m.value)>1 AND instr(lower(?),lower(m.value))>0) OR (k.kind='scene' AND json_extract(k.data,'$.file')=? AND json_extract(k.data,'$.scene')=?)) ORDER BY CASE k.state WHEN 'confirmed' THEN 0 WHEN 'review' THEN 2 ELSE 1 END,k.id DESC LIMIT 80",
            (
                record["project"],
                source,
                dump(sorted(speakers)),
                source,
                record["file"],
                record["scene"],
            ),
        ).fetchall()
    facts, questions = [], []
    used = 0
    for row in rows:
        data = json.loads(row["data"])
        if (
            row["kind"] != "scene"
            and row["subject"] not in speakers
            and not any(
                mentioned(term, source) for term in [row["subject"], *data["mentions"]]
            )
        ):
            continue
        if row["kind"] == "scene" and (
            data["file"] != record["file"] or data["scene"] != record["scene"]
        ):
            continue
        item = {
            "kind": row["kind"],
            "subject": row["subject"],
            "attribute": row["attribute"],
            "value": row["value"],
            "status": row["state"],
            "evidence": data["evidence"],
        }
        if row["state"] == "review":
            if len(questions) < 5:
                questions.append(
                    {
                        "subject": row["subject"],
                        "question": data["reason"],
                        "instruction": "Unresolved. Do not assume either interpretation.",
                    }
                )
            continue
        size = len(dump(item))
        if len(facts) < 12 and used + size <= 5000:
            facts.append(item)
            used += size
    if not facts and not questions:
        return {}
    return {
        "project_memory": facts,
        "context_questions": questions,
        "memory_guidance": "Source and explicit project/speaker rules take precedence. auto entries are fallible AI observations, not confirmed facts. Never infer missing gender or resolve context_questions by guessing. Scene-specific changes override global summaries.",
    }
