"""Authenticated loopback client shared by the CLI and stdio MCP."""

import argparse
import base64
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from app_inspection import discovery_path


def inspect_app(database, screenshot=False):
    database = Path(database).resolve()
    try:
        endpoint = json.loads(discovery_path(database).read_text(encoding="utf-8"))
        port = endpoint["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Некорректный порт")
        if Path(endpoint["database"]).resolve() != database:
            raise ValueError("Не совпадает база приложения")
        token = database.with_name("server.token").read_text(encoding="ascii").strip()
        route = "screenshot" if screenshot else "state"
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/inspection/{route}",
            headers={"X-Studio-Token": token},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=35) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                details = json.load(error)
            raise ValueError(
                details.get("error", "Ошибка локального сервера")
            ) from error
        if result.get("session") != endpoint["session"]:
            raise ValueError("Сессия окна изменилась; повторите запрос")
        return result
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError(
            "Не удалось прочитать открытое окно. Нужна запущенная версия с поддержкой inspection и та же база (--db). "
            + str(error)
        ) from error


def cli_main(database, action, output=None):
    try:
        if action == "screenshot" and not output:
            raise ValueError("Для снимка укажите --output <путь.png>")
        result = inspect_app(database, action == "screenshot")
        if action == "screenshot":
            target = Path(output).resolve()
            with target.open("xb") as stream:
                stream.write(
                    base64.b64decode(result.pop("image")["data"], validate=True)
                )
            result["screenshot"] = str(target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    from paths import DEFAULT_DB

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["state", "screenshot"])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output")
    args = parser.parse_args()
    sys.exit(cli_main(args.db, args.action, args.output))
