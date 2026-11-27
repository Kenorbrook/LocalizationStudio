"""Interruptible waits between attempts of a single cloud request."""

import time
from datetime import timezone
from email.utils import parsedate_to_datetime

from long_text import JobStopped

BACKOFF = (15, 30, 60, 120, 240, 300)


def retry_delay(header, attempt, now):
    if attempt >= len(BACKOFF):
        return None
    delay = BACKOFF[attempt]
    if header:
        try:
            value = header.strip()
            if value.isdigit():
                seconds = int(value)
            else:
                date = parsedate_to_datetime(value)
                seconds = (
                    date if date.tzinfo else date.replace(tzinfo=timezone.utc)
                ).timestamp() - now
            return max(delay, seconds)
        except (ValueError, TypeError, OverflowError, OSError):
            pass
    return delay


def wait_before_retry(store, job, settings, error, attempt, started, emit):
    from providers import ServiceUnavailable

    delay = retry_delay(error.retry_after, attempt, time.time())
    if delay is None:
        return False
    identity = settings.get("cloud_connection") or ""

    def check():
        with store.db() as db:
            row = db.execute(
                "SELECT j.state,coalesce(c.retry_unavailable,1) enabled "
                "FROM jobs j LEFT JOIN cloud_connections c ON c.id=? WHERE j.id=?",
                (identity, job),
            ).fetchone()
        if not row or row["state"] != "running":
            raise JobStopped("остановлено")
        return row["enabled"]

    if not check():
        return False
    if delay > 86400:
        raise ServiceUnavailable(f"{error}. API просит подождать более суток")
    deadline = time.monotonic() + delay
    run_minutes = float(settings.get("run_minutes", 0))
    if run_minutes and deadline >= started + run_minutes * 60:
        raise ServiceUnavailable("Лимит времени запуска истечёт до повтора API")
    with store.db() as db:
        db.execute(
            "UPDATE jobs SET retry_at=?,error=? WHERE id=? AND state='running'",
            (time.time() + delay, str(error), job),
        )
    emit(f"API: {error} · повтор {attempt + 1}/{len(BACKOFF)} через {delay:.0f} с")
    try:
        while check():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(1, remaining))
        return False
    finally:
        with store.db() as db:
            db.execute(
                "UPDATE jobs SET error=CASE WHEN state='running' THEN '' ELSE error END,retry_at=0 WHERE id=?",
                (job,),
            )
