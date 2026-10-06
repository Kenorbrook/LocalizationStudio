"""Local direct Ollama and optional HTTPS Chat Completions compatible providers."""

import ctypes, json, os, subprocess, time, urllib.request, urllib.error
import re
from ctypes import wintypes
from urllib.parse import urlparse
from core import POLICY_PATH, SHARED, TOKEN, dump, validate
from speaker_context import GUIDANCE, project_rules
from long_text import estimate_tokens, OutputTruncated, RequestTooLarge

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServiceUnavailable(Exception):
    pass


class Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def secret(project, value=None):
    if os.name != "nt":
        raise ValueError("Хранилище ключей этой сборки поддерживает Windows")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(Credential)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    name = f"LocalizationStudio/project/{project}"
    if value is not None:
        blob = value.encode("utf-8")
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = Credential(
            Type=1,
            TargetName=name,
            CredentialBlobSize=len(blob),
            CredentialBlob=buf,
            Persist=2,
            UserName="api",
        )
        if not api.CredWriteW(ctypes.byref(cred), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return True
    ptr = ctypes.POINTER(Credential)()
    if not api.CredReadW(name, 1, 0, ctypes.byref(ptr)):
        return ""
    try:
        return ctypes.string_at(
            ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize
        ).decode("utf-8")
    finally:
        api.CredFree(ptr)


def json_request(url, data=None, headers=None, local=False):
    req = urllib.request.Request(
        url,
        None if data is None else dump(data).encode(),
        {"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with (DIRECT if local else urllib.request.build_opener()).open(
            req, timeout=180 if data else 4
        ) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in {429, 500, 502, 503, 504}:
            raise ServiceUnavailable(
                f"Провайдер временно недоступен: HTTP {e.code}"
            ) from None
        # Response bodies can contain echoed authentication data: do not log them.
        raise ServiceUnavailable(
            f"Ошибка провайдера HTTP {e.code}; проверьте адрес, доступ и ключ"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ServiceUnavailable(
            "Нет связи с моделью. Прогресс сохранён; можно продолжить"
        ) from None


def models():
    try:
        inventory = json_request("http://127.0.0.1:11434/api/tags", local=True)[
            "models"
        ]
        return {
            "models": [m["name"] for m in inventory],
            "model_details": inventory,
            "online": True,
        }
    except ServiceUnavailable:
        return {"models": [], "online": False}


class Provider:
    def __init__(self, project, kind, settings, diagnostic=False):
        self.project = project
        self.kind = kind
        self.settings = settings
        self.calls = 0
        self.profile = json.loads(
            (SHARED / "local_literary_profile.json").read_text(encoding="utf-8-sig")
        )
        self.policy = POLICY_PATH.read_text(encoding="utf-8-sig")
        self.rules = project_rules(project)
        if diagnostic:
            return
        if any(
            not self.fits_request(mode, [])
            for mode in ("translate", "critic", "verify")
        ):
            raise ServiceUnavailable(
                "Правила проекта и резерв ответа не помещаются в контекст; увеличьте размер контекста или уменьшите лимит ответа"
            )
        if kind == "local" and not models()["online"]:
            print(
                "Waiting for local Ollama at 127.0.0.1:11434. VPN is not required.",
                flush=True,
            )
            try:
                subprocess.run(
                    ["ollama", "ps"],
                    timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            for attempt in range(20):
                if models()["online"]:
                    break
                time.sleep(1)
            else:
                raise ServiceUnavailable("Ollama не запустилась; очередь сохранена")

    def prompt(self, mode):
        source = self.settings.get("source_language", "English")
        target = self.settings.get("target_language", "Russian")
        prompt = self.profile[
            {
                "translate": "translate_prompt",
                "critic": "critic_prompt",
                "verify": "verify_prompt",
            }[mode]
        ]
        # Profile is Russian literary guidance; language choice remains explicit.
        prompt += (
            f"\nThe requested language pair is {source} -> {target}. Adapt language-specific advice accordingly.\nPROJECT RULES:\n"
            + dump(self.rules)
        )
        prompt += "\nPOLICY:\n" + self.policy
        prompt += "\nSPEAKER AND CONTEXT GUIDANCE:\n" + GUIDANCE
        if mode in {"critic", "verify"}:
            prompt += "\nIf an entry has human_review_flag, treat its translation as suspect, never as a trusted example. Check meaning against the source and surrounding context, speaker references, grammar, register, omissions, additions, script alphabet and protected placeholders. Do not invent a correction merely because a flag exists. Explain concrete problems or why the translation is valid."
            if self.settings.get("marked_review"):
                prompt += (
                    "\nADDITIONAL HUMAN REVIEW INSTRUCTIONS:\n"
                    + self.settings.get("review_instruction", "")
                )
        return prompt

    def fits_request(self, mode, entries):
        budget = (
            int(self.settings.get("context", 8192))
            - int(self.settings.get("max_output", 1200))
            - 384
        )
        return (
            estimate_tokens(self.prompt(mode))
            + estimate_tokens(dump({"items": entries}))
            <= budget
        )

    def call(self, mode, entries):
        if not self.fits_request(mode, entries):
            raise RequestTooLarge("Запрос превышает оценочный бюджет контекста модели")
        self.calls += 1
        limit = int(self.settings.get("max_calls", 1000))
        if self.kind in {"cloud", "mcp"} and self.calls > limit:
            raise ServiceUnavailable(
                "Достигнут лимит внешних запросов для этого запуска"
            )
        prompt = self.prompt(mode)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": dump({"items": entries})},
        ]
        if self.kind == "local":
            options = {
                **self.profile["sampling_options"],
                **self.profile["parameters"],
                "num_ctx": int(self.settings.get("context", 8192)),
            }
            options.pop("chunk_items", None)
            options.pop("chunk_chars", None)
            options.pop("context_lines", None)
            options["num_predict"] = int(
                self.settings.get("max_output", options.get("num_predict", 1200))
            )
            result = json_request(
                "http://127.0.0.1:11434/api/chat",
                {
                    "model": self.settings.get("model", "qwen3.8:latest"),
                    "messages": messages,
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": options,
                },
                local=True,
            )
            text = result["message"]["content"]
            if result.get("done_reason") in {"length", "max_tokens", "maxTokens"}:
                raise OutputTruncated("Модель достигла лимита ответа")
        elif self.kind == "mcp":
            from mcp_sampling import generate

            try:
                text, self.last_model = generate(self.settings, prompt, entries)
            except RuntimeError as e:
                raise ServiceUnavailable(str(e)) from None
        else:
            endpoint = self.settings.get("endpoint", "").rstrip("/")
            if urlparse(endpoint).scheme != "https":
                raise ValueError("Облачный API должен использовать HTTPS")
            key = secret(self.project["id"])
            if not key:
                raise ServiceUnavailable("Не задан API-ключ")
            result = json_request(
                endpoint + "/chat/completions",
                {
                    "model": self.settings.get("cloud_model", ""),
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "max_tokens": int(self.settings.get("max_output", 1200)),
                },
                headers={"Authorization": "Bearer " + key},
            )
            text = result["choices"][0]["message"]["content"]
            if result["choices"][0].get("finish_reason") in {
                "length",
                "max_tokens",
                "maxTokens",
            }:
                raise OutputTruncated("Модель достигла лимита ответа")
        text = text.strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        return json.loads(text)


def process_record(provider, stage, entry):
    from long_text import process

    return process(provider, stage, entry, _process_record_direct)


def _process_record_direct(provider, stage, entry):
    from local_editor import one_item, review_entries, register_error

    def check(source, text):
        try:
            validate(source, text)
            target = (
                getattr(provider, "settings", {})
                .get("target_language", "Russian")
                .lower()
            )
            if target in {"russian", "ru", "русский"}:
                error = register_error(source, text)
                if error:
                    return error
                source_words = set(
                    re.findall(r"[A-Za-zА-Яа-яЁё]+", TOKEN.sub("", source))
                )
                for word in re.findall(r"[A-Za-zА-Яа-яЁё]+", TOKEN.sub("", text)):
                    if (
                        re.search("[A-Za-z]", word)
                        and re.search("[А-Яа-яЁё]", word)
                        and word not in source_words
                    ):
                        return "Смешаны латиница и кириллица внутри слова: " + word
                if (
                    len(re.findall(r"[A-Za-z]+", source)) > 3
                    and len(re.findall(r"[A-Za-z]+", text)) > 3
                    and not re.search("[А-Яа-яЁё]", text)
                ):
                    return "Ожидался русский перевод, получен английский текст"
            return ""
        except ValueError as e:
            return str(e)

    if stage == "translate":
        if (
            not entry.get("translate_symbols")
            and entry["english"].strip()
            and not any(character.isalnum() for character in entry["english"])
        ):
            validate(entry["english"], entry["english"])
            return (
                entry["english"],
                "Невербальная строка: знаки оригинала сохранены без запроса к модели",
            )
        text = one_item(provider.call("translate", [entry]), entry["id"]).get("text")
        error = check(entry["english"], text)
        if error:
            raise ValueError(error)
        return text, ""
    events = []
    result = review_entries([entry], provider.call, check, events.append)
    if any(e.get("status") in {"uncertain", "rejected", "unchanged"} for e in events):
        raise ValueError("Редактор не подтвердил результат: " + dump(events))
    item = one_item(result, entry["id"])
    validate(entry["english"], item["text"])
    return item["text"], dump(events)
