"""Content-free progress shared by the isolated runner and the desktop supervisor."""

import json
import threading
import time
from pathlib import Path

REQUEST_TIMEOUT = 1800
RUN_BUDGET = 1500
MAX_TOKENS = 1000
MAX_ITERATIONS = 4
STAGES = {
    "startup": "Запускаем среду Hermes",
    "connecting": "Подключаем инструменты проекта",
    "waiting": "Модель обрабатывает запрос",
    "tool": "Читаем данные проекта",
    "answer": "Получаем ответ модели",
    "cleanup": "Завершаем работу Hermes",
    "done": "Исследование завершено",
}


class Progress:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.stage = "startup"
        self.step = 0
        self.characters = 0
        self.last_write = 0

    def emit(self, stage, force=True):
        with self.lock:
            changed = self.stage != stage
            self.stage = stage
            now = time.monotonic()
            if not force and not changed and now - self.last_write < 1:
                return
            self.last_write = now
            data = {"stage": stage, "step": self.step, "characters": self.characters}
            temporary = self.path.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(data), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                pass  # Telemetry must not abort a completed model response.
            if force or changed:
                print(
                    "[Hermes] "
                    + STAGES[stage]
                    + (f" · шаг {self.step}" if self.step else ""),
                    flush=True,
                )

    def model_step(self, step, previous=None):
        with self.lock:
            self.step = max(1, int(step))
        self.emit("waiting")

    def delta(self, text):
        if isinstance(text, str) and text:
            with self.lock:
                self.characters += len(text)
            self.emit("answer", force=False)

    def tool_start(self, *args):
        self.emit("tool")

    def tool_complete(self, *args):
        self.emit("waiting")


def progress_message(path, elapsed):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        stage = data["stage"]
        if stage not in STAGES:
            return None
        step, characters = data.get("step", 0), data.get("characters", 0)
        if (
            type(step) is not int
            or type(characters) is not int
            or not 0 <= step <= 100
            or not 0 <= characters <= 10000000
        ):
            return None
        result = STAGES[stage] + f" · {int(elapsed)//60:02}:{int(elapsed)%60:02}"
        if step:
            result += f" · шаг {step}"
        if characters:
            result += f" · получено {characters} символов"
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None
