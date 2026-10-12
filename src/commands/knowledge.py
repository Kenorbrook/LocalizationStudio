"""Human review of extracted project context."""

from project_knowledge import decide


def knowledge_decision(store, data):
    return decide(
        store,
        int(data["project"]),
        int(data["id"]),
        int(data["revision"]),
        data["state"],
        data.get("value"),
    )
