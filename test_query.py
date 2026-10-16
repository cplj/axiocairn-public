"""Affected query-evidence behavior only; trusted local synthetic fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from sdk import RecoverySDK
from ordinary_script import OrdinaryScript
from demo import FaultTool, scenario


class QueryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.resources = []

    def tearDown(self):
        for resource in self.resources:
            resource.db.close()
        self.tmp.cleanup()

    def pending(self, cls=RecoverySDK):
        runner = cls(str(self.root / (cls.__name__+'.db')))
        tool = FaultTool(str(self.root / (cls.__name__+'tool.db')))
        self.resources.extend((runner, tool))
        tool.query_available = False
        self.assertEqual(runner.run('action', tool, {'amount': 1}, 'v1', 'timeout_after')['state'], 'UNKNOWN')
        tool.query_available = True
        return runner, tool

    def check_scenario(self, failure):
        results = []
        for cls in (RecoverySDK, OrdinaryScript):
            target = self.root / cls.__name__
            target.mkdir()
            results.append(scenario(cls, target, failure))
        self.assertEqual(results[0], results[1])

    def test_timeout_repeated_run_no_effect_and_later_receipt_recovery(self):
        self.check_scenario('timeout')

    def test_actual_malformed_json_stays_manual_after_reopen_and_repair(self):
        self.check_scenario('malformed')

    def test_actual_nan_receipt_stays_manual_after_reopen_and_repair(self):
        self.check_scenario('nonfinite')

    def test_connection_unavailable_remains_unknown_without_effect(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            tool.query_error = ConnectionError('unavailable')
            for _ in range(2):
                self.assertEqual(runner.run('action', tool, {'amount': 1}, 'v1')['state'], 'UNKNOWN')
            self.assertEqual(tool.execute_calls, 1)
            tool.query_error = None
            self.assertEqual(runner.reconcile('action', tool, 'v1')['state'], 'COMPLETED')

    def test_correct_receipt_recovery_unchanged(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            self.assertEqual(runner.reconcile('action', tool, 'v1')['state'], 'COMPLETED')
            self.assertEqual(tool.execute_calls, 1)

    def test_unexpected_runtime_error_propagates_without_state_change(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            before = runner.inspect('action')
            tool.query_error = RuntimeError('programming failure')
            with self.assertRaises(RuntimeError):
                runner.reconcile('action', tool, 'v1')
            self.assertEqual(runner.inspect('action'), before)
            self.assertEqual(tool.execute_calls, 1)

    def test_plain_query_valueerror_is_not_misclassified_as_bad_json(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            before = runner.inspect('action')
            tool.query_error = ValueError('programming failure')
            with self.assertRaises(ValueError):
                runner.reconcile('action', tool, 'v1')
            self.assertEqual(runner.inspect('action'), before)
            self.assertEqual(tool.execute_calls, 1)

    def test_wrong_account_and_version_are_checked_before_query(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            other = FaultTool(str(self.root / (cls.__name__+'other.db')), 'account-b')
            self.resources.append(other)
            other.query_error = TimeoutError('must not query')
            with self.assertRaises(ValueError):
                runner.reconcile('action', other, 'v1')
            with self.assertRaises(ValueError):
                runner.run('action', other, {'amount': 1}, 'v1')
            self.assertEqual((other.queries, other.execute_calls), (0, 0))
            before = tool.queries
            self.assertEqual(runner.reconcile('action', tool, 'v2')['state'], 'VERSION_MISMATCH')
            self.assertEqual(tool.queries, before)

    def test_manual_stop_cannot_bypass_identity_or_version_checks(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            tool.query_error = json.JSONDecodeError('broken', '{', 1)
            self.assertEqual(runner.reconcile('action', tool, 'v1')['state'], 'MANUAL_REVIEW')
            before = runner.inspect('action')
            tool.account = 'other'
            with self.assertRaises(ValueError):
                runner.reconcile('action', tool, 'v1')
            tool.account = 'account-a'
            self.assertEqual(runner.reconcile('action', tool, 'v2')['state'], 'VERSION_MISMATCH')
            self.assertEqual(runner.inspect('action'), before)
            tool.query_error = RuntimeError('must not query stopped task')
            self.assertEqual(runner.reconcile('action', tool, 'v1'), before)

    def test_duplicate_receipts_stop_even_when_one_is_nonfinite(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner, tool = self.pending(cls)
            with tool.db:
                tool.db.execute('INSERT INTO receipts VALUES (?, ?, ?)', (tool.identity, 'action', 'NaN'))
            self.assertEqual(runner.reconcile('action', tool, 'v1')['state'], 'MANUAL_REVIEW')
            self.assertEqual(tool.execute_calls, 1)


if __name__ == '__main__':
    unittest.main()
