"""Core error recovery presentation; independent of optional agents."""

from error_recovery import preference, plan, apply, categories


def recovery(store, project=0, enabled=None, start=False, kinds=None):
    if enabled is not None:
        preference(store, project, enabled)
    if kinds is not None:
        categories(store, project, kinds)
    if start:
        if getattr(store, "hermes_inference", False):
            raise ValueError("Сначала завершите работу локального агента")
        apply(store, project)
    result = {
        "automatic_retry": False,
        "retry_categories": [],
        "retry_plan": {"rows": []},
        "busy": bool(getattr(store, "hermes_inference", False)),
    }
    if project:
        with store.db() as db:
            result["busy"] = result["busy"] or bool(
                db.execute(
                    "SELECT 1 FROM jobs WHERE project=? AND (worker_active=1 OR state IN ('running','queued','paused','held'))",
                    (project,),
                ).fetchone()
            )
        result["automatic_retry"] = preference(store, project)
        result["retry_categories"] = categories(store, project)
        result["retry_plan"] = plan(store, project)
        for row in result["retry_plan"]["rows"]:
            row["parameters"] = {
                key: row["settings"].get(key)
                for key in ["context", "context_before", "context_after", "max_output"]
            }
            del row["settings"]
    return result
