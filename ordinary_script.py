"""Small persisted-intent comparator for the ledger demo cases only."""
import json
import sqlite3
from sdk import canonical


class OrdinaryScript:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, tool TEXT, payload TEXT, version TEXT, state TEXT)")
        self.db.commit()

    def inspect(self, task_id):
        return dict(self.db.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone())

    def _state(self, task_id, state):
        with self.db:
            self.db.execute("UPDATE jobs SET state=? WHERE id=?", (state, task_id))
        return self.inspect(task_id)

    def run(self, task_id, tool, payload, version, fault=None):
        encoded = canonical(payload)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, ?, 'PREPARED')",
                            (task_id, tool.identity, encoded, version))
        job = self.inspect(task_id)
        if job['tool'] != tool.identity or job['payload'] != encoded:
            raise ValueError('immutable intent conflict')
        if job['version'] != version:
            return dict(job, state='VERSION_MISMATCH')
        if job['state'] in {'COMPLETED', 'MANUAL_REVIEW'}:
            return job
        if job['state'] != 'PREPARED':
            return self.reconcile(task_id, tool, version)
        self._state(task_id, 'IN_FLIGHT')
        try:
            tool.execute(task_id, payload, fault)
        except TimeoutError:
            self._state(task_id, 'UNKNOWN')
        return self.reconcile(task_id, tool, version)

    def reconcile(self, task_id, tool, version):
        job = self.inspect(task_id)
        if job['tool'] != tool.identity:
            raise ValueError('tool identity conflict')
        if job['version'] != version:
            return dict(job, state='VERSION_MISMATCH')
        if job['state'] == 'MANUAL_REVIEW':
            return job
        try:
            receipts = tool.query(task_id)
        except (ConnectionError, TimeoutError):
            return self._state(task_id, 'UNKNOWN')
        except json.JSONDecodeError:
            return self._state(task_id, 'MANUAL_REVIEW')
        if len(receipts) == 1:
            try:
                encoded_receipt = canonical(receipts[0])
            except ValueError:
                return self._state(task_id, 'MANUAL_REVIEW')
            if encoded_receipt == job['payload']:
                return self._state(task_id, 'COMPLETED')
        return self._state(task_id, 'MANUAL_REVIEW')
