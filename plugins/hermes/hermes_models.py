"""Prepare an Ollama context profile from existing local weights, without inference."""

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

from .hermes_install import guard_root


def configured_context(details):
    parameters = details.get("parameters", "")
    if isinstance(parameters, dict):
        value = parameters.get("num_ctx")
        return value if type(value) is int else None
    matches = re.findall(r"^\s*num_ctx\s+(\d+)\s*$", str(parameters), re.MULTILINE)
    return int(matches[-1]) if matches else None


class HermesModels:
    def __init__(self, base_url="http://127.0.0.1:11434"):
        if urlparse(base_url).hostname != "127.0.0.1":
            raise ValueError("Для Hermes разрешён только локальный Ollama")
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, payload=None, timeout=10):
        request = urllib.request.Request(
            self.base_url + "/api/" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            raise ValueError(
                f"Ollama отклонила запрос (HTTP {error.code}). Проверьте выбранную модель и журнал Ollama."
            ) from None
        except OSError:
            raise ValueError(
                "Ollama недоступна. Запустите Ollama и обновите список моделей."
            ) from None

    def inventory(self):
        with self.request("tags") as response:
            return json.load(response).get("models", [])

    def listing(self):
        try:
            return {"models": self.inventory(), "online": True, "error": ""}
        except ValueError as error:
            return {"models": [], "online": False, "error": str(error)}

    def details(self, model):
        with self.request("show", {"model": model}) as response:
            return json.load(response)

    def validate(self, model, context, configured=False):
        if (
            not isinstance(model, str)
            or not model
            or len(model) > 160
            or any(c in model for c in "\r\n\x00")
        ):
            raise ValueError("Выберите установленную модель Ollama")
        if type(context) is not int or not 64000 <= context <= 131072:
            raise ValueError("Контекст Hermes должен быть от 64000 до 131072 токенов")
        item = next(
            (item for item in self.inventory() if item.get("name") == model), None
        )
        if not item:
            raise ValueError("Модель не найдена среди установленных. Обновите список.")
        details = self.details(model)
        if model.endswith(":cloud") or any(
            item.get(key) or details.get(key) for key in ("remote_host", "remote_model")
        ):
            raise ValueError(
                "Выберите локальную модель: облачные модели здесь не подготавливаются"
            )
        if "tools" not in details.get("capabilities", []):
            raise ValueError("Модель не поддерживает вызов инструментов Hermes")
        limits = [
            value
            for key, value in details.get("model_info", {}).items()
            if key.endswith(".context_length") and type(value) is int
        ]
        if not limits or max(limits) < context:
            raise ValueError(
                "Модель не подтверждает поддержку выбранного контекста Hermes"
            )
        if configured and configured_context(details) != context:
            raise ValueError(
                "Контекст Ollama отличается от настроек Hermes. Нажмите «Подготовить для Hermes»."
            )
        return item, details

    def prepare(self, root, model, context, cancelled, progress):
        item, details = self.validate(model, context)
        if configured_context(details) == context:
            return {"model": model, "context": context, "source": model, "reused": True}
        guard_root(root)
        owners = root / "model_profiles.json"
        if owners.is_symlink() or owners.is_junction():
            raise ValueError("Реестр профилей модели не должен быть ссылкой")
        try:
            saved = json.loads(owners.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError):
            saved = {}
        key = hashlib.sha256(
            json.dumps([model, item.get("digest"), context]).encode()
        ).hexdigest()
        inventory = {row["name"]: row for row in self.inventory()}
        previous = saved.get(key)
        if isinstance(previous, dict):
            name = previous.get("model")
            existing = inventory.get(name, {})
            if existing and existing.get("digest") == previous.get("digest"):
                self.validate(name, context, configured=True)
                return {
                    "model": name,
                    "context": context,
                    "source": model,
                    "reused": True,
                }
        name = "studio-hermes-" + key[:16] + ":latest"
        while name in inventory:
            name = "studio-hermes-" + key[:16] + "-" + uuid.uuid4().hex[:8] + ":latest"
        if cancelled.is_set():
            raise ValueError("Подготовка остановлена")
        progress("Создаём профиль Ollama с контекстом " + str(context) + "…")
        with self.request(
            "create",
            {
                "model": name,
                "from": model,
                "parameters": {"num_ctx": context},
                "stream": True,
            },
            timeout=30,
        ) as response:
            deadline = time.monotonic() + 180
            complete = False
            for _ in range(2000):
                if cancelled.is_set():
                    raise ValueError(
                        "Подготовка остановлена; сохранённая модель Hermes не изменена"
                    )
                if time.monotonic() > deadline:
                    raise ValueError("Ollama не завершила подготовку за три минуты")
                line = response.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    raise ValueError("Некорректный ответ Ollama")
                value = json.loads(line)
                if value.get("error"):
                    raise ValueError(
                        "Ollama не смогла создать профиль выбранной модели"
                    )
                if value.get("status") == "success":
                    complete = True
                    break
            if not complete:
                raise ValueError("Ollama не подтвердила завершение подготовки")
        target, _ = self.validate(name, context, configured=True)
        if cancelled.is_set():
            raise ValueError("Подготовка остановлена")
        saved[key] = {"model": name, "digest": target.get("digest")}
        root.mkdir(parents=True, exist_ok=True)
        temporary = owners.with_suffix(".tmp")
        if temporary.is_symlink() or temporary.is_junction():
            raise ValueError("Временный реестр профилей не должен быть ссылкой")
        temporary.write_text(json.dumps(saved), encoding="utf-8")
        temporary.replace(owners)
        return {"model": name, "context": context, "source": model, "reused": False}
