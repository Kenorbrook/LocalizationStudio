"""Track worker exit and recover orphaned queues without starting or killing jobs."""

import ctypes
import os
import subprocess
import time


def process_alive(pid):
    if not pid or int(pid) <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        code = ctypes.c_ulong()
        return (
            bool(code.value == 259)
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            else None
        )
    finally:
        kernel.CloseHandle(handle)


def recover_workers(store):
    timestamp = time.time()
    with store.db() as db:
        jobs = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM jobs WHERE worker_active=1 OR state IN ('queued','running')"
            )
        ]
    recovered = []
    for job in jobs:
        heartbeat = job["heartbeat"] or 0
        if not heartbeat:
            try:
                heartbeat = time.mktime(
                    time.strptime(job["created"], "%Y-%m-%d %H:%M:%S")
                )
            except (ValueError, TypeError):
                continue
        # Allow startup and the periodic heartbeat to settle before checking old sessions.
        if timestamp - heartbeat < (15 if job["pid"] else 60):
            continue
        if process_alive(job["pid"]) is not False:
            continue
        target = "paused" if job["state"] in {"queued", "running"} else job["state"]
        message = job["error"]
        if target == "paused" and not message:
            message = "Обработчик завершился; очередь сохранена. Нажмите «Продолжить»."
        with store.db() as db:
            updated = db.execute(
                "UPDATE jobs SET state=?,worker_active=0,error=? WHERE id=? AND state=? AND pid IS ? AND heartbeat=? AND worker_active=?",
                (
                    target,
                    message,
                    job["id"],
                    job["state"],
                    job["pid"],
                    job["heartbeat"],
                    job["worker_active"],
                ),
            )
            if updated.rowcount:
                db.execute(
                    "UPDATE mcp_requests SET state='failed',response='Обработчик завершился; запрос прекращён' WHERE job=? AND state IN ('pending','sent')",
                    (job["id"],),
                )
                recovered.append(job["id"])
    return recovered


def watch_worker(store, jid, process, launch_token=None):
    # Python's Windows venv launcher can own a different PID from its worker child.
    owner_column = "worker_launch" if launch_token else "pid"
    owner = launch_token if launch_token else process.pid
    try:
        try:
            code = process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            with store.db() as db:
                db.execute(
                    f"UPDATE jobs SET state='paused',error='Обработчик не подтвердил запуск за 45 секунд; очередь сохранена' WHERE id=? AND state='queued' AND {owner_column}=?",
                    (jid, owner),
                )
            # A slow model is already claimed as running. Keep watching its actual process.
            code = process.wait()
    except OSError:
        return
    with store.db() as db:
        job = db.execute(
            f"SELECT state,error FROM jobs WHERE id=? AND {owner_column}=?",
            (jid, owner),
        ).fetchone()
        if not job:
            return
        target = "paused" if job["state"] in {"queued", "running"} else job["state"]
        message = job["error"]
        if job["state"] in {"queued", "running"}:
            message = f"Обработчик завершился неожиданно (код {code}); очередь сохранена. Нажмите «Продолжить»."
        db.execute(
            f"UPDATE jobs SET state=?,worker_active=0,error=? WHERE id=? AND {owner_column}=?",
            (target, message, jid, owner),
        )
        db.execute(
            "UPDATE mcp_requests SET state='failed',response='Обработчик завершился; запрос прекращён' WHERE job=? AND state IN ('pending','sent')",
            (jid,),
        )
    if code == 0 and target == "incomplete":
        from error_recovery import after_job

        try:
            after_job(store, jid)
        except Exception:
            # The original errors and completed queue remain available for manual retry.
            import logging

            logging.exception("Не удалось подготовить автоматический повтор ошибок")
