"""Bounded negotiation: rejected wording is feedback for a fresh, verified proposal."""

import json

MAX_VARIANTS = 3
MAX_CALLS = 7


class Unresolved(ValueError):
    def __init__(self, reason, attempts, calls):
        super().__init__(reason)
        self.attempts = attempts
        self.calls = calls


def resolve(snapshot, request, cancelled, progress):
    from .error_resolution import parse, prompt

    attempts, seen = [], set()
    calls = 0
    checked_original = False
    original_confirmations = []
    last_reason = "Решение не подтверждено"

    def ask(proposal=None, feedback=None, phase="proposal"):
        nonlocal calls
        if cancelled.is_set():
            raise Unresolved("Разбор остановлен", attempts, calls)
        if calls >= MAX_CALLS:
            raise Unresolved(
                "Достигнут лимит запросов. " + last_reason, attempts, calls
            )
        calls += 1
        progress(
            {
                "round": len(attempts),
                "calls": calls,
                "max_calls": MAX_CALLS,
                "max_variants": MAX_VARIANTS,
                "phase": phase,
                "attempts": attempts,
            }
        )
        try:
            response = request(prompt(snapshot, proposal, feedback=feedback))
            if cancelled.is_set():
                raise Unresolved("Разбор остановлен", attempts, calls)
            return parse(response, snapshot, verification=proposal or False)
        except Unresolved:
            raise
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise Unresolved(str(error), attempts, calls) from None

    for number in range(1, MAX_VARIANTS + 1):
        # Only previous rounds are feedback, not the empty current round.
        feedback = (
            json.loads(json.dumps(attempts, ensure_ascii=False)) if attempts else None
        )
        attempt = {"round": number}
        attempts.append(attempt)
        proposal = ask(feedback=feedback)
        attempt["proposal"] = proposal
        if proposal["action"] == "uncertain":
            raise Unresolved(proposal["reason"], attempts, calls)
        key = " ".join(proposal["text"].split()).casefold()
        if key in seen:
            last_reason = (
                "Повторён уже рассмотренный вариант; нужна другая формулировка"
            )
            attempt["rejection"] = last_reason
            continue
        seen.add(key)
        if proposal["action"] == "keep":
            checked_original = True
        verification = ask(proposal, feedback=feedback, phase="verify")
        attempt["verification"] = verification
        if verification["verdict"] == "confirm":
            progress(
                {
                    "round": number,
                    "calls": calls,
                    "max_calls": MAX_CALLS,
                    "max_variants": MAX_VARIANTS,
                    "phase": "accepted",
                    "attempts": attempts,
                }
            )
            return proposal, verification, attempts, calls
        last_reason = "Проверка решения: " + verification["reason"]
        if verification["verdict"] == "keep" and proposal["action"] == "fix":
            original_confirmations.append(verification)
            if len(original_confirmations) >= 2 and snapshot["row"]["text"]:
                try:
                    keep = parse(
                        json.dumps(
                            {
                                "record": snapshot["row"]["id"],
                                "revision": snapshot["row"]["revision"],
                                "action": "keep",
                                "text": snapshot["row"]["text"],
                                "reason": "Две отдельные проверки подтвердили текущий перевод. "
                                + verification["reason"],
                                "evidence": verification["evidence"],
                            }
                        ),
                        snapshot,
                    )
                except ValueError as error:
                    raise Unresolved(str(error), attempts, calls) from None
                confirmed = {
                    **{
                        key: value
                        for key, value in verification.items()
                        if key != "assessment"
                    },
                    "verdict": "confirm",
                    "reason": keep["reason"],
                    "confirmation_method": "two_keep_checks",
                    "checks": original_confirmations,
                }
                attempt["accepted_original"] = {
                    "proposal": keep,
                    "verification": confirmed,
                }
                return keep, confirmed, attempts, calls
        if (
            verification["verdict"] == "keep"
            and proposal["action"] == "fix"
            and not checked_original
            and snapshot["row"]["text"]
        ):
            checked_original = True
            try:
                keep = parse(
                    json.dumps(
                        {
                            "record": snapshot["row"]["id"],
                            "revision": snapshot["row"]["revision"],
                            "action": "keep",
                            "text": snapshot["row"]["text"],
                            "reason": verification["reason"],
                            "evidence": verification["evidence"],
                        }
                    ),
                    snapshot,
                )
            except ValueError as error:
                last_reason = "Текущий перевод не прошёл формальную проверку: " + str(
                    error
                )
                attempt["rejection"] = last_reason
                continue
            seen.add(" ".join(keep["text"].split()).casefold())
            keep_check = ask(keep, feedback=feedback, phase="keep_check")
            attempt["keep_check"] = {"proposal": keep, "verification": keep_check}
            if keep_check["verdict"] == "confirm":
                return keep, keep_check, attempts, calls
            last_reason = (
                "Проверка сохранения текущего перевода: " + keep_check["reason"]
            )
        # Disagreement is now input to a new wording, never permission to bypass verification.
        attempt["rejection"] = last_reason
    raise Unresolved(
        f"Не удалось согласовать перевод за {MAX_VARIANTS} варианта. {last_reason}",
        attempts,
        calls,
    )
