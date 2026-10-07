"""Read-only API routes, separate from HTTP authentication and framing."""

from providers import models as available_models
from queries import (
    state as project_state,
    records as file_records,
    queue_page,
    record_context,
    job_log as read_job_log,
)


def state(store, q, port):
    data = project_state(store, int(q.get("project", 0)), q.get("editing") == "1")
    return data


def completed_projects(store, q, port):
    data = store.completed_projects()
    return data


def hidden_projects(store, q, port):
    data = store.hidden_projects()
    return data


def records(store, q, port):
    data = file_records(
        store,
        int(q["file"]),
        int(q.get("offset", 0)),
        q.get("search", ""),
        q.get("status", ""),
    )
    return data


def queue(store, q, port):
    data = queue_page(store, int(q["project"]))
    return data


def process_history(store, q, port):
    from process_view import history_page

    data = history_page(store, int(q["project"]), int(q.get("offset", 0)))
    return data


def process_settings(store, q, port):
    from process_view import preferences

    data = preferences(store, int(q["project"]))
    return data


def preserved(store, q, port):
    from preservation import folder

    data = folder(store, int(q["project"]), int(q.get("offset", 0)))
    return data


def marked(store, q, port):
    from process_view import marks_page

    data = marks_page(
        store,
        int(q["project"]),
        q["kind"],
        int(q.get("offset", 0)),
        [int(value) for value in q.get("retained", "").split(",") if value],
    )
    return data


def connections(store, q, port):
    from mcp_server import connections

    data = {
        "mcp": connections(store),
        "application": f"127.0.0.1:{port}",
    }
    return data


def models(store, q, port):
    data = available_models()
    return data


def history(store, q, port):
    with store.db() as db:
        data = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM history WHERE record=? ORDER BY id DESC LIMIT 50",
                (int(q["id"]),),
            )
        ]
    return data


def proposals(store, q, port):
    with store.db() as db:
        data = [
            dict(r)
            for r in db.execute(
                "SELECT p.*,r.source,r.text current FROM proposals p JOIN records r ON r.id=p.record JOIN files f ON f.id=r.file WHERE f.project=? AND p.state='pending' ORDER BY p.id DESC LIMIT 100",
                (int(q["project"]),),
            )
        ]
    return data


def current(store, q, port):
    data = store.record(int(q["id"]))
    return data


def context(store, q, port):
    data = record_context(store, int(q["id"]), int(q.get("radius", 10)))
    return data


def project_analysis(store, q, port):
    from game_detection import saved_report

    data = {"report": saved_report(store, int(q["project"]))}
    return data


def job_log(store, q, port):
    data = read_job_log(store, int(q["id"]))
    return data


def errors(store, q, port):
    with store.db() as db:
        data = [
            dict(r)
            for r in db.execute(
                "SELECT e.*,r.source,r.text,j.stage,j.settings retry_settings,j.provider retry_provider FROM errors e LEFT JOIN records r ON r.id=e.record LEFT JOIN jobs j ON j.id=e.job WHERE e.project=? AND e.resolved=0 ORDER BY e.id DESC LIMIT 100 OFFSET ?",
                (int(q["project"]), int(q.get("offset", 0))),
            )
        ]
    from error_workflow import fill_budgets

    data = fill_budgets(store, data)
    return data


ROUTES = {
    "/api/state": state,
    "/api/completed-projects": completed_projects,
    "/api/hidden-projects": hidden_projects,
    "/api/records": records,
    "/api/queue": queue,
    "/api/process-history": process_history,
    "/api/process-settings": process_settings,
    "/api/preserved": preserved,
    "/api/marked": marked,
    "/api/connections": connections,
    "/api/models": models,
    "/api/history": history,
    "/api/proposals": proposals,
    "/api/current": current,
    "/api/context": context,
    "/api/project-analysis": project_analysis,
    "/api/job-log": job_log,
    "/api/errors": errors,
}


class UnknownRoute(ValueError):
    pass


def read_request(store, path, query, port):
    try:
        handler = ROUTES[path]
    except KeyError:
        raise UnknownRoute("Не найдено") from None
    return handler(store, query, port)
