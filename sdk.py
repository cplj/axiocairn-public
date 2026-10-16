"""Bounded simulated-tool recovery SDK; standard library only."""
import json
import sqlite3
if __package__:
    from .limits import MAX_JSON, MAX_RECEIPTS, MAX_RECEIPT_BYTES, InputLimitError, check_size
else:
    from limits import MAX_JSON, MAX_RECEIPTS, MAX_RECEIPT_BYTES, InputLimitError, check_size


def canonical(value):
    chunks, size = [], 0
    for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > MAX_JSON:
            raise InputLimitError(f"JSON exceeds {MAX_JSON} bytes")
        chunks.append(chunk)
    return "".join(chunks)


class SimulatedTool:
    """ledger deduplicates action IDs; append deliberately does not."""
    def __init__(self, path, name, kind, account):
        if kind not in {"ledger", "append"}:
            raise ValueError("unknown tool kind")
        if not isinstance(account, str) or not account.strip():
            raise ValueError("account scope required")
        self.name, self.kind, self.account = name, kind, account
        self.idempotent = kind == "ledger"
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (name TEXT, action TEXT, payload TEXT)")
        self.db.commit()
        self.query_available = True

    @property
    def identity(self):
        return canonical([self.name, self.kind, self.account])

    def query(self, action):
        if not self.query_available:
            raise ConnectionError("receipt query unavailable")
        receipts, total_bytes = [], 0
        for row in self.db.execute(
                "SELECT CASE WHEN length(CAST(payload AS BLOB)) <= ? THEN payload ELSE NULL END, "
                "length(CAST(payload AS BLOB)) FROM receipts WHERE name=? AND action=? LIMIT ?",
                (MAX_JSON, self.identity, action, MAX_RECEIPTS + 1)):
            if len(receipts) >= MAX_RECEIPTS:
                raise InputLimitError(f"receipt count exceeds {MAX_RECEIPTS}")
            if row[1] is not None and row[1] > MAX_JSON:
                raise InputLimitError(f"receipt exceeds {MAX_JSON} bytes")
            total_bytes += row[1] or 0
            if total_bytes > MAX_RECEIPT_BYTES:
                raise InputLimitError(f"receipt total exceeds {MAX_RECEIPT_BYTES} bytes")
            if not isinstance(row[0], str):
                raise json.JSONDecodeError("receipt payload must be a string", "", 0)
            receipts.append(json.loads(row[0]))
        return receipts

    def execute(self, action, payload, fault=None):
        if fault == "timeout_before":
            raise TimeoutError("before effect")
        # SQLite serializes this toy ledger's key check and effect together.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.db.execute("SELECT CASE WHEN length(CAST(payload AS BLOB)) <= ? THEN payload ELSE NULL END "
                                   "FROM receipts WHERE name=? AND action=? LIMIT 1",
                                   (MAX_JSON, self.identity, action)).fetchall()
            if self.idempotent and rows:
                if rows[0][0] != canonical(payload):
                    raise ValueError("action payload conflict")
            else:
                self.db.execute("INSERT INTO receipts VALUES (?, ?, ?)",
                                (self.identity, action, canonical(payload)))
        if fault == "crash_after":
            raise SystemExit("simulated process exit after external effect")
        if fault == "timeout_after":
            raise TimeoutError("after effect")


class RecoverySDK:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, tool TEXT, payload TEXT, version TEXT,
            state TEXT, detail TEXT)""")
        self.db.commit()

    def status(self, task_id):
        check_size(task_id)
        lengths = self.db.execute(
            "SELECT length(CAST(id AS BLOB)), length(CAST(tool AS BLOB)), "
            "length(CAST(payload AS BLOB)), length(CAST(version AS BLOB)), "
            "length(CAST(state AS BLOB)), length(CAST(detail AS BLOB)) FROM tasks WHERE id=?",
            (task_id,)).fetchone()
        if lengths is not None and any(length is not None and length > MAX_JSON for length in lengths):
            raise InputLimitError(f"stored intent field exceeds {MAX_JSON} bytes")
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def inspect(self, task_id):
        """Compatibility with the initial source distribution's status accessor."""
        return self.status(task_id)

    def _state(self, task_id, state, detail):
        with self.db:
            self.db.execute("UPDATE tasks SET state=?, detail=? WHERE id=?", (state, detail, task_id))
        return self.status(task_id)

    def run(self, task_id, tool, payload, version, fault=None):
        if not all(isinstance(x, str) and x.strip() for x in (task_id, version)):
            raise ValueError("task ID and version required")
        check_size(task_id)
        check_size(version)
        encoded = canonical(payload)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tasks VALUES (?, ?, ?, ?, 'PREPARED', '')",
                            (task_id, tool.identity, encoded, version))
        task = self.status(task_id)
        if task["tool"] != tool.identity or task["payload"] != encoded:
            raise ValueError("immutable intent conflict")
        if task["version"] != version:
            # Preserve the stored state; mismatch is a response, never a migration.
            return dict(task, state="VERSION_MISMATCH", detail="stored version differs; review required")
        if task["state"] == "COMPLETED" or task["state"] == "MANUAL_REVIEW":
            return task
        if task["state"] != "PREPARED":
            task = self.reconcile(task_id, tool, version)
            if task["state"] != "RETRY_SAFE":
                return task
        self._state(task_id, "IN_FLIGHT", "intent committed before external call")
        try:
            tool.execute(task_id, payload, fault)
        except TimeoutError:
            self._state(task_id, "UNKNOWN", "execution timed out; query before retry")
        return self.reconcile(task_id, tool, version)

    def reconcile(self, task_id, tool, version):
        task = self.status(task_id)
        if task["tool"] != tool.identity:
            raise ValueError("tool identity conflict")
        if task["version"] != version:
            return dict(task, state="VERSION_MISMATCH", detail="stored version differs; review required")
        if task["state"] == "MANUAL_REVIEW":
            return task
        try:
            receipts = tool.query(task_id)
        except (ConnectionError, TimeoutError):
            if task["state"] == "COMPLETED":
                return dict(task, state="MANUAL_REVIEW",
                            detail="receipt query unavailable; stored COMPLETED preserved; do not retry")
            return self._state(task_id, "UNKNOWN", "receipt query unavailable; no retry")
        except InputLimitError as error:
            return self._state(task_id, "MANUAL_REVIEW", str(error) + "; do not retry")
        except (json.JSONDecodeError, RecursionError):
            return self._state(task_id, "MANUAL_REVIEW", "malformed receipt JSON; do not retry")
        if len(receipts) == 1:
            try:
                encoded_receipt = canonical(receipts[0])
            except (ValueError, RecursionError):
                return self._state(task_id, "MANUAL_REVIEW", "noncanonical receipt; do not retry")
            if encoded_receipt == task["payload"]:
                return self._state(task_id, "COMPLETED", "one matching effect observed")
        if receipts:
            return self._state(task_id, "MANUAL_REVIEW", "duplicate or conflicting receipts")
        if task["state"] == "COMPLETED":
            return dict(task, state="MANUAL_REVIEW",
                        detail="no receipt; stored COMPLETED preserved; do not retry")
        if task["state"] == "PREPARED":
            return task
        if tool.idempotent:
            return self._state(task_id, "RETRY_SAFE", "no receipt; adapter guarantees key deduplication")
        return self._state(task_id, "MANUAL_REVIEW", "no receipt cannot prove non-execution; do not retry")
