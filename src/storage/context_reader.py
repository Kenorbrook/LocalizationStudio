"""Read-only workspace port used by context and token estimation."""


class ContextReader:
    def __init__(self, database, records):
        self.database = database
        self.records = records

    def db(self):
        return self.database.connect()

    def record(self, rid):
        return self.records.record(rid)
