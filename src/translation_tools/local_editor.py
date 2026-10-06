"""Conservative local editing; no game-specific roles or cloud dependency."""

from __future__ import annotations
import collections
import re

STOP = set(
    "a an the i me my you your he him his she her it its we our they their to of in on at for with and or but is was were be been this that when how".split()
)
STOP.update(
    "are here there such say said ask asked do does did don isn not no why what one still now then very only where".split()
)
ERROR_TYPES = {
    "meaning",
    "roles",
    "gender",
    "negation",
    "tense",
    "omission",
    "addition",
    "register",
    "idiom",
    "grammar",
}


def register_error(source, text):
    # Narrow alert, not a replacement dictionary. Borderline idiomatic uses go
    # to the review journal rather than getting an automatically inserted swear.
    if re.search(r"\b(?:fuck\w*|bullshit|motherfuck\w*)\b", source, re.I):
        coarse = r"(?:бля|ёб|еба|ебу|ебл|ебн|пизд|ху[йяеёюи]|хер|хрен|трах|дерьм|говн|сра|сру|сука|суч|мудак|мудил|жоп)"
        if not re.search(coarse, text, re.I):
            return "possible softened profanity: source has fuck/bullshit; preserve its coarse register without adding intensity; if ambiguous, leave for human review"
    return None


def reference_examples(profile, entries):
    query = (
        set(re.findall(r"[a-z]+", " ".join(e["english"] for e in entries).lower()))
        - STOP
    )
    scored = []
    for index, example in enumerate(profile.get("reference_examples", [])):
        words = set(re.findall(r"[a-z]+", example["english"].lower())) - STOP
        overlap = query & words
        if overlap:
            scored.append(
                (len(overlap) / max(1, len(words)), len(overlap), -index, example)
            )
    scored.sort(key=lambda x: x[:3], reverse=True)
    return [x[3] for x in scored[:2]]


def refusal_error(source, text):
    # A character saying "I can't help" is valid fiction. Match model meta replies.
    patterns = [
        r"как (?:языковая|искусственная) модель",
        r"я (?:не могу|не буду) (?:переводить|перевести|обрабатывать) (?:этот|данный|подобный|такой) (?:текст|контент|материал)",
        r"as an (?:ai|language model)",
        r"i (?:cannot|can.t) (?:translate|assist with) (?:this|that) (?:content|text|request)",
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.I) and not re.search(pattern, source, re.I):
            return "model refusal/meta commentary instead of translation"
    return None


def one_item(response, item_id):
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise ValueError("response must contain items array")
    matches = [
        x for x in response["items"] if isinstance(x, dict) and x.get("id") == item_id
    ]
    if len(matches) != 1 or len(response["items"]) != 1:
        raise ValueError("expected exactly one requested id")
    return matches[0]


def review_entries(entries, call, validate, trace):
    """Fresh calls for diagnosis, source-first rewrite, independent selection.

    call(stage, entries) -> response; trace captures every non-keep decision.
    Malformed responses raise, allowing the caller's bounded retry/resume logic.
    Uncertainty preserves the existing text and remains visible in the journal.
    """
    outputs = []
    for entry in entries:
        item_id, source, original = entry["id"], entry["english"], entry["russian"]
        diagnosis = one_item(call("critic", [entry]), item_id)
        draft_error = validate(source, original)
        if draft_error:
            diagnosis = {
                "id": item_id,
                "decision": "repair",
                "error_type": "register" if "profanity" in draft_error else "meaning",
                "evidence": source,
                "reason": draft_error,
            }
        decision = diagnosis.get("decision")
        if decision not in {"keep", "repair", "uncertain"}:
            raise ValueError("invalid critic decision")
        if decision == "keep":
            outputs.append({"id": item_id, "text": original, "reason": ""})
            continue
        evidence, reason = diagnosis.get("evidence"), diagnosis.get("reason")
        event = {
            "id": item_id,
            "english": source,
            "original": original,
            "diagnosis": diagnosis,
        }
        if (
            decision == "uncertain"
            or diagnosis.get("error_type") not in ERROR_TYPES
            or not isinstance(evidence, str)
            or not evidence.strip()
            or evidence not in source
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            event["status"] = "uncertain"
            trace(event)
            outputs.append(
                {
                    "id": item_id,
                    "text": original,
                    "reason": "uncertain; preserved original",
                }
            )
            continue
        target = {k: v for k, v in entry.items() if k != "russian"}
        target["diagnosis"] = diagnosis
        for candidate_attempt in range(2):
            candidate = one_item(call("translate", [target]), item_id).get("text")
            if not isinstance(candidate, str):
                raise ValueError("candidate text is not a string")
            event["candidate"] = candidate
            error = validate(source, candidate)
            if not error:
                break
            event.setdefault("invalid_candidates", []).append(
                {"text": candidate, "error": error}
            )
            target["validation_feedback"] = error
        if error:
            event.update(status="rejected", reason=error)
            trace(event)
            outputs.append(
                {
                    "id": item_id,
                    "text": original,
                    "reason": "candidate rejected: " + error,
                }
            )
            continue
        if candidate == original:
            event["status"] = "unchanged"
            trace(event)
            outputs.append({"id": item_id, "text": original, "reason": ""})
            continue
        verification = {**entry, "original": original, "candidate": candidate}
        verification.pop("russian", None)
        verdict = one_item(call("verify", [verification]), item_id)
        if (
            verdict.get("winner") not in {"original", "candidate", "uncertain"}
            or not isinstance(verdict.get("reason"), str)
            or not verdict["reason"].strip()
        ):
            raise ValueError("invalid verification verdict")
        accepted = verdict["winner"] == "candidate"
        event.update(
            verdict=verdict,
            status=(
                "accepted"
                if accepted
                else ("uncertain" if verdict["winner"] == "uncertain" else "rejected")
            ),
        )
        trace(event)
        outputs.append(
            {
                "id": item_id,
                "text": candidate if accepted else original,
                "reason": verdict["reason"],
            }
        )
    return {"items": outputs}
