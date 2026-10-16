import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sdk import RecoverySDK, SimulatedTool


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sdk = RecoverySDK(self.root / "tasks.db")

    def tearDown(self):
        self.sdk.db.close()
        self.tmp.cleanup()

    def tool(self, kind):
        tool = SimulatedTool(self.root / "external.db", "external", kind, 'account-a')
        self.addCleanup(tool.db.close)
        return tool

    def test_normal_repeat_preserves_single_effect_both_tools(self):
        for kind in ("ledger", "append"):
            tool = self.tool(kind)
            for _ in range(2):
                self.assertEqual(self.sdk.run(kind, tool, {"amount": 5}, "v1")["state"], "COMPLETED")
            self.assertEqual(len(tool.query(kind)), 1)

    def test_crash_after_reopen_reconciles_without_duplicate_both_tools(self):
        for kind in ("ledger", "append"):
            tool = self.tool(kind)
            with self.assertRaises(SystemExit):
                self.sdk.run(kind, tool, {"amount": 5}, "v1", "crash_after")
            self.assertEqual(self.sdk.inspect(kind)["state"], "IN_FLIGHT")
            self.sdk.db.close()
            tool.db.close()
            self.sdk = RecoverySDK(self.root / "tasks.db")
            tool = self.tool(kind)
            self.assertEqual(self.sdk.run(kind, tool, {"amount": 5}, "v1")["state"], "COMPLETED")
            self.assertEqual(len(tool.query(kind)), 1)

    def test_timeout_after_queries_before_retry(self):
        tool = self.tool("append")
        self.assertEqual(self.sdk.run("t", tool, {"x": 1}, "v1", "timeout_after")["state"], "COMPLETED")
        self.assertEqual(len(tool.query("t")), 1)

    def test_timeout_before_nonidempotent_stops(self):
        tool = self.tool("append")
        self.assertEqual(self.sdk.run("t", tool, {}, "v1", "timeout_before")["state"], "MANUAL_REVIEW")
        self.sdk.run("t", tool, {}, "v1")
        self.assertEqual(tool.query("t"), [])

    def test_timeout_before_idempotent_safe_retry(self):
        tool = self.tool("ledger")
        self.assertEqual(self.sdk.run("t", tool, {}, "v1", "timeout_before")["state"], "RETRY_SAFE")
        self.assertEqual(self.sdk.run("t", tool, {}, "v1")["state"], "COMPLETED")
        self.assertEqual(len(tool.query("t")), 1)

    def test_unavailable_query_no_blind_retry(self):
        tool = self.tool("append")
        tool.query_available = False
        self.assertEqual(self.sdk.run("t", tool, {}, "v1", "timeout_after")["state"], "UNKNOWN")
        self.assertEqual(self.sdk.run("t", tool, {}, "v1")["state"], "UNKNOWN")
        tool.query_available = True
        self.assertEqual(len(tool.query("t")), 1)
        self.assertEqual(self.sdk.run("t", tool, {}, "v1")["state"], "COMPLETED")

    def test_version_change_blocks_new_effect_and_preserves_state(self):
        tool = self.tool("append")
        with self.assertRaises(SystemExit):
            self.sdk.run("t", tool, {}, "v1", "crash_after")
        self.assertEqual(self.sdk.run("t", tool, {}, "v2")["state"], "VERSION_MISMATCH")
        self.assertEqual(self.sdk.inspect("t")["state"], "IN_FLIGHT")
        self.assertEqual(len(tool.query("t")), 1)

    def test_intent_and_tool_conflicts_block(self):
        tool = self.tool("append")
        self.sdk.run("t", tool, {"x": 1}, "v1")
        with self.assertRaises(ValueError):
            self.sdk.run("t", tool, {"x": 2}, "v1")
        with self.assertRaises(ValueError):
            self.sdk.run("t", self.tool("ledger"), {"x": 1}, "v1")
        self.assertEqual(len(tool.query("t")), 1)

    def test_completed_empty_evidence_preserves_original_both_tools(self):
        for kind in ('ledger', 'append'):
            tool = self.tool(kind)
            before = self.sdk.run(kind, tool, {'amount': 5}, 'v1')
            with patch.object(tool, 'query', return_value=[]):
                result = self.sdk.reconcile(kind, tool, 'v1')
            self.assertEqual(result['state'], 'MANUAL_REVIEW')
            self.assertEqual(self.sdk.inspect(kind), before)
            self.assertEqual(self.sdk.run(kind, tool, {'amount': 5}, 'v1'), before)
            self.assertEqual(len(tool.query(kind)), 1)

    def test_completed_unavailable_evidence_preserves_original_both_tools(self):
        for kind in ('ledger', 'append'):
            tool = self.tool(kind)
            before = self.sdk.run(kind, tool, {'amount': 5}, 'v1')
            for error in (ConnectionError('unavailable'), TimeoutError('timeout')):
                with patch.object(tool, 'query', side_effect=error):
                    result = self.sdk.reconcile(kind, tool, 'v1')
                self.assertEqual(result['state'], 'MANUAL_REVIEW')
                self.assertEqual(self.sdk.inspect(kind), before)
            self.assertEqual(self.sdk.reconcile(kind, tool, 'v1'), before)
            self.assertEqual(len(tool.query(kind)), 1)

    def test_conflicting_and_duplicate_receipts_need_review(self):
        for duplicate in (False, True):
            tool = self.tool("append")
            task_id = str(duplicate)
            with self.assertRaises(SystemExit):
                self.sdk.run(task_id, tool, {"x": 1}, "v1", "crash_after")
            if duplicate:
                tool.execute(task_id, {"x": 1})
            else:
                with tool.db:
                    tool.db.execute("UPDATE receipts SET payload='{}' WHERE action=?", (task_id,))
            self.assertEqual(self.sdk.run(task_id, tool, {"x": 1}, "v1")["state"], "MANUAL_REVIEW")


if __name__ == "__main__":
    unittest.main(verbosity=2)
