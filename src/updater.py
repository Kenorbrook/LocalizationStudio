"""Standalone Windows helper; waits for the GUI to release its files."""

import argparse
import ctypes
import json
import subprocess
import time
from pathlib import Path
from update_installer import read_plan, replace_application


def wait_for_exit(pid, timeout=90):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            raise RuntimeError("Приложение ещё не завершилось; файлы не изменены")
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    plan = read_plan(args.plan)
    status = Path(args.plan).with_name("result.json")
    changed = False
    try:
        wait_for_exit(plan["gui_pid"])
        replace_application(plan)
        changed = True
        status.write_text(
            json.dumps({"installed": True, "version": plan["version"]}),
            encoding="utf-8",
        )
    except Exception as error:
        status.write_text(
            json.dumps({"installed": False, "error": str(error)}, ensure_ascii=False),
            encoding="utf-8",
        )
        ctypes.windll.user32.MessageBoxW(
            None,
            "Обновление не установлено. "
            + str(error)
            + "\nПредыдущая версия сохранена.",
            "Localization Studio",
            0x10,
        )
    finally:
        if Path(plan["home"], "LocalizationStudio.exe").exists():
            subprocess.Popen(
                [
                    str(Path(plan["home"]) / "LocalizationStudio.exe"),
                    "--db",
                    plan["database"],
                    "--resume-update",
                    str(Path(args.plan).resolve()),
                ],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )


if __name__ == "__main__":
    main()
