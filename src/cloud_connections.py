"""Application-wide cloud connections. Credential material stays in Windows."""

import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def preserve_legacy(old, new, replacing=False):
    """Remember the server of a pre-existing project credential when switching."""
    endpoint = old.get("_legacy_cloud_endpoint")
    if not endpoint and (
        not old.get("cloud_connection")
        or str(old["cloud_connection"]).startswith("legacy:")
    ):
        endpoint = old.get("endpoint")
    if replacing:
        endpoint = new.get("endpoint")
    if endpoint:
        new["_legacy_cloud_endpoint"] = endpoint


def base_url(value):
    url = str(value).strip().rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or any(c.isspace() or ord(c) < 32 for c in url)
    ):
        raise ValueError("Укажите HTTPS-адрес API без параметров и учётных данных")
    return url


def listing(store):
    from providers import secret

    with store.db() as db:
        connections = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM cloud_connections ORDER BY name COLLATE NOCASE, id"
            )
        ]
        projects = [
            dict(row)
            for row in db.execute(
                "SELECT id,name,settings FROM projects WHERE deleting=0 ORDER BY id"
            )
        ]
    for connection in connections:
        connection["projects"] = json.loads(connection["projects"])
        connection["models"] = json.loads(connection["models"])
    for project in projects:
        settings = json.loads(project["settings"])
        endpoint = settings.get("_legacy_cloud_endpoint")
        if not endpoint and not settings.get("cloud_connection"):
            endpoint = settings.get("endpoint")
        if endpoint and secret(project["id"]):
            connections.append(
                {
                    "id": f"legacy:{project['id']}",
                    "name": project["name"],
                    "endpoint": endpoint,
                    "projects": [project["id"]],
                    "models": (
                        [settings["cloud_model"]] if settings.get("cloud_model") else []
                    ),
                    "legacy": True,
                }
            )
    return connections


def save(store, values):
    from providers import secret

    identity = str(values.get("id") or uuid.uuid4().hex)
    name = str(values.get("name", "")).strip()
    key = str(values.get("key", "")).strip()
    projects = values.get("projects")
    if not name or len(name) > 100 or len(key.encode()) > 2500:
        raise ValueError("Проверьте название и длину ключа")
    if projects is not None and (
        not isinstance(projects, list)
        or not projects
        or any(type(i) is not int or i < 1 for i in projects)
    ):
        raise ValueError("Выберите проекты или разрешите подключение всем")
    with store.db() as db:
        old = db.execute(
            "SELECT endpoint FROM cloud_connections WHERE id=?", (identity,)
        ).fetchone()
        endpoint = base_url(values.get("endpoint", ""))
        if old:
            if endpoint != old["endpoint"] or key:
                raise ValueError("Для другого адреса или ключа добавьте подключение")
            db.execute(
                "UPDATE cloud_connections SET name=?,projects=? WHERE id=?",
                (name, json.dumps(projects), identity),
            )
        else:
            if values.get("id") or not key:
                raise ValueError("Укажите API-ключ нового подключения")
            secret("cloud/" + identity, key)
            db.execute(
                "INSERT INTO cloud_connections(id,name,endpoint,projects,models) VALUES (?,?,?,?,?)",
                (identity, name, endpoint, json.dumps(projects), "[]"),
            )
    return identity


def connection(store, identity):
    match = next((c for c in listing(store) if c["id"] == identity), None)
    if match is None:
        raise ValueError("API-подключение не найдено")
    return match


def credential(identity):
    from providers import secret

    if identity.startswith("legacy:"):
        return secret(int(identity.split(":", 1)[1]))
    return secret("cloud/" + identity)


def discover(store, identity):
    item = connection(store, identity)
    key = credential(identity)
    if not key:
        raise ValueError("Ключ подключения недоступен")
    request = urllib.request.Request(
        base_url(item["endpoint"]) + "/models",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=12
        ) as response:
            payload = json.loads(response.read(2_000_000))
        models = sorted(
            {
                entry["id"]
                for entry in payload["data"]
                if isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and 0 < len(entry["id"]) <= 255
            },
            key=str.casefold,
        )
    except urllib.error.HTTPError as error:
        raise ValueError(f"Список моделей недоступен (HTTP {error.code})") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            "API не вернул список моделей. Можно указать ID вручную."
        ) from None
    if not item.get("legacy"):
        with store.db() as db:
            db.execute(
                "UPDATE cloud_connections SET models=? WHERE id=?",
                (json.dumps(models), identity),
            )
    return models


def resolve(database, identity, project, endpoint):
    with closing(
        sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        if identity == f"legacy:{project}":
            old = db.execute(
                "SELECT settings FROM projects WHERE id=?", (project,)
            ).fetchone()
            saved = json.loads(old[0]) if old else {}
            original = saved.get("_legacy_cloud_endpoint") or saved.get("endpoint", "")
            if not original or base_url(original) != base_url(endpoint):
                raise ValueError("Адрес сохранённого ключа проекта изменился")
            return credential(identity)
        row = db.execute(
            "SELECT endpoint,projects FROM cloud_connections WHERE id=?", (identity,)
        ).fetchone()
    if not row or row[0] != base_url(endpoint):
        raise ValueError("Выберите API-подключение для этого адреса")
    scope = json.loads(row[1])
    if scope is not None and project not in scope:
        raise ValueError("Это API-подключение недоступно для проекта")
    return credential(identity)


def job_key(database, project, settings):
    from providers import secret

    identity = settings.get("cloud_connection")
    if not identity:
        return secret(project)
    return resolve(database, identity, project, settings.get("endpoint", ""))
