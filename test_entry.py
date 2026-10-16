import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sdk import RecoverySDK, SimulatedTool
from ordinary_script import OrdinaryScript
from recover_existing import recover, open_existing


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, ordinary=False):
        intent = self.root / ('ordinary.db' if ordinary else 'intent.db')
        receipts = self.root / ('ordinary-receipts.db' if ordinary else 'receipts.db')
        runner = (OrdinaryScript if ordinary else RecoverySDK)(str(intent))
        tool = SimulatedTool(str(receipts), 'ledger', 'ledger', 'account-a')
        try:
            runner.run('action', tool, {'amount': 1}, 'v1', 'crash_after')
        except SystemExit:
            pass
        finally:
            runner.db.close()
            tool.db.close()
        return intent, receipts

    def test_missing_intent_does_not_create(self):
        intent, receipt = self.root / 'missing.db', self.root / 'receipt.db'
        with self.assertRaises(sqlite3.OperationalError):
            recover(intent, receipt, 'ledger', 'ledger', 'account-a', 'action', 'v1')
        self.assertFalse(intent.exists())
        self.assertFalse(receipt.exists())

    def test_missing_receipt_does_not_create(self):
        intent, _ = self.fixture()
        receipt = self.root / 'missing-receipt.db'
        with self.assertRaises(sqlite3.OperationalError):
            recover(intent, receipt, 'ledger', 'ledger', 'account-a', 'action', 'v1')
        self.assertFalse(receipt.exists())

    def test_existing_match_no_execution_and_comparator_tie(self):
        intent, receipt = self.fixture()
        oi, ot = self.fixture(ordinary=True)
        with patch.object(SimulatedTool, 'execute', side_effect=AssertionError('no execution')):
            result = recover(intent, receipt, 'ledger', 'ledger', 'account-a', 'action', 'v1')
            ordinary = OrdinaryScript.__new__(OrdinaryScript)
            ordinary.db = open_existing(oi)
            ordinary.db.row_factory = sqlite3.Row
            tool = SimulatedTool(str(ot), 'ledger', 'ledger', 'account-a')
            try:
                comparison = ordinary.reconcile('action', tool, 'v1')
            finally:
                ordinary.db.close()
                tool.db.close()
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(result['state'], comparison['state'])

    def test_normal_initialization_still_creates(self):
        intent, receipt = self.root / 'new-intent.db', self.root / 'new-receipt.db'
        runner = RecoverySDK(str(intent))
        tool = SimulatedTool(str(receipt), 'ledger', 'ledger', 'account-a')
        runner.db.close()
        tool.db.close()
        self.assertTrue(intent.exists())
        self.assertTrue(receipt.exists())

    def test_wrong_account_stops(self):
        intent, receipt = self.fixture()
        with self.assertRaisesRegex(ValueError, 'identity conflict'):
            recover(intent, receipt, 'ledger', 'ledger', 'account-b', 'action', 'v1')

    def test_wrong_version_stops(self):
        intent, receipt = self.fixture()
        result = recover(intent, receipt, 'ledger', 'ledger', 'account-a', 'action', 'v2')
        self.assertEqual(result['state'], 'VERSION_MISMATCH')


if __name__ == '__main__':
    unittest.main()
