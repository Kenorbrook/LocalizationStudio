"""Compatibility facade composing transactional workspace repositories."""

from paths import ROOT, APP_HOME, UI_ROOT, POLICY_PATH, SHARED
from serialization import now, dump
from extraction import extract, literal
from validation import TOKEN, validate
from storage.database import Database
from storage.projects import ProjectsRepository
from storage.records import RecordsRepository
from storage.jobs import JobsRepository
from storage.files import FilesRepository


class Store:
    def __init__(self, path):
        self.database = Database(path)
        self.path = self.database.path
        self.projects = ProjectsRepository(self.database)
        self.records = RecordsRepository(self.database)
        self.jobs = JobsRepository(self.database, self.records)
        self.files = FilesRepository(self.database, self.records)
        with self.db() as db:
            needs_preservation = not db.execute(
                "SELECT 1 FROM schema_versions WHERE name='preservation-v1'"
            ).fetchone()
        if needs_preservation:
            self.classify_preserved()
            with self.db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO schema_versions VALUES ('preservation-v1')"
                )

    def db(self):
        return self.database.connect()

    @staticmethod
    def next_id(db, table):
        return Database.next_id(db, table)

    def project(self, root):
        return self.projects.project(root)

    def hide_project(self, pid, hidden=True):
        return self.projects.hide_project(pid, hidden)

    def archive_completed(self, editing_project=0):
        return self.projects.archive_completed(editing_project)

    def complete_project(self, pid):
        return self.projects.complete_project(pid)

    def completed_projects(self):
        return self.projects.completed_projects()

    def delete_project_data(self, pid):
        return self.projects.delete_project_data(pid)

    def hidden_projects(self):
        return self.projects.hidden_projects()

    def record(self, rid):
        return self.records.record(rid)

    def preserve(self, rid, revision, kind, reason, worker_job=None):
        return self.records.preserve(rid, revision, kind, reason, worker_job)

    def restore_preserved(self, rid, revision):
        return self.records.restore_preserved(rid, revision)

    def classify_preserved(self, project=None):
        return self.records.classify_preserved(project)

    def mark(self, rid, revision, kind, origin="human"):
        return self.records.mark(rid, revision, kind, origin)

    def update(self, rid, revision, text, status, reviewer, reason="", manual=False):
        return self.records.update(
            rid, revision, text, status, reviewer, reason, manual
        )

    def undo(self, rid, revision):
        return self.records.undo(rid, revision)

    def clear_marks(self, project, kind):
        return self.records.clear_marks(project, kind)

    def create_job(
        self,
        project,
        stage,
        provider,
        settings,
        file=None,
        retry=False,
        mark_kind=None,
        allow_manual=False,
        record_ids=None,
        defer=False,
    ):
        return self.jobs.create_job(
            project,
            stage,
            provider,
            settings,
            file,
            retry,
            mark_kind,
            allow_manual,
            record_ids,
            defer,
        )

    def error(self, job, record, message):
        return self.jobs.error(job, record, message)

    def import_files(self, project, paths):
        return self.files.import_files(project, paths)

    def export(self, fid, destination, apply=False):
        return self.files.export(fid, destination, apply)
