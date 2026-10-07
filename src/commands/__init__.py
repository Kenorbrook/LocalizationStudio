"""Explicit command registry. Domain handlers do not depend on HTTP."""

from . import projects, records, jobs, settings

HANDLERS = {
    "complete-project": projects.complete_project,
    "delete-project": projects.delete_project,
    "hide-project": projects.hide_project,
    "restore-project": projects.restore_project,
    "project": projects.project,
    "analyze-project": projects.analyze_project,
    "import-detected": projects.import_detected,
    "import": projects.import_files,
    "upload": projects.upload,
    "export": projects.export,
    "manual-error": records.manual_error,
    "translate-preserved": records.translate_preserved,
    "preserved-check": records.preserved_check,
    "preserve": records.preserve,
    "restore-preserved": records.restore_preserved,
    "clear-marks": records.clear_marks,
    "mark": records.mark,
    "save": records.save,
    "undo": records.undo,
    "unverify": records.unverify,
    "unlock": records.unlock,
    "proposal": records.proposal,
    "enqueue-error-retry": jobs.enqueue_error_retry,
    "error-retry-plan": jobs.error_retry_plan,
    "error-retry-budget": jobs.error_retry_budget,
    "error-budget": jobs.error_budget,
    "retry-errors": jobs.retry_errors,
    "job": jobs.job,
    "control": jobs.control,
    "process-settings": settings.process_settings,
    "language-preview": settings.language_preview,
    "speaker-profiles": settings.speaker_profiles,
    "settings": settings.settings,
}


def dispatch(store, action, data):
    try:
        handler = HANDLERS[action]
    except KeyError:
        raise ValueError("Неизвестная команда") from None
    return handler(store, data)
