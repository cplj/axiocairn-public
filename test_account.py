"""Affected account-scope behavior; no real provider access."""
import tempfile
import unittest
from pathlib import Path
from sdk import RecoverySDK, SimulatedTool
from ordinary_script import OrdinaryScript
from demo import scenario


class CountedTool(SimulatedTool):
    def __init__(self, *args):
        super().__init__(*args)
        self.queries = self.effects = 0

    def query(self, action):
        self.queries += 1
        return super().query(action)

    def execute(self, *args):
        self.effects += 1
        return super().execute(*args)


class AccountBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.resources = []

    def tearDown(self):
        for resource in self.resources:
            resource.db.close()
        self.tmp.cleanup()

    def tool(self, account, name='tool', kind='ledger', file='tool.db'):
        tool = CountedTool(str(self.root / file), name, kind, account)
        self.resources.append(tool)
        return tool

    def runner(self, cls=RecoverySDK):
        runner = cls(str(self.root / (cls.__name__ + '.db')))
        self.resources.append(runner)
        return runner

    def test_recovery_and_fair_comparator(self):
        results = []
        for cls in (RecoverySDK, OrdinaryScript):
            directory = self.root / cls.__name__
            directory.mkdir()
            results.append(scenario(cls, directory, 'timeout'))
        self.assertEqual(results[0], results[1])

    def test_required_nonempty_account_before_database_open(self):
        for account in (None, '', '  ', 1, []):
            with self.assertRaisesRegex(ValueError, 'account scope required'):
                self.tool(account)
        self.assertFalse((self.root / 'tool.db').exists())
        with self.assertRaises(TypeError):
            SimulatedTool(str(self.root / 'tool.db'), 'tool', 'ledger')

    def test_tuple_identity_has_no_delimiter_collision(self):
        a = self.tool('y:ledger:z', name='x')
        b = self.tool('z', name='x:ledger:y')
        self.assertNotEqual(a.identity, b.identity)

    def test_wrong_account_cannot_consume_matching_other_receipt(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner = self.runner(cls)
            a = self.tool('a', file=cls.__name__+'a.db')
            b = self.tool('b', file=cls.__name__+'b.db')
            a.query_available = False
            self.assertEqual(runner.run('id', a, {'x': 1}, 'v1', 'timeout_before')['state'], 'UNKNOWN')
            b.execute('id', {'x': 1})
            before = (b.queries, b.effects, runner.inspect('id'))
            with self.assertRaises(ValueError):
                runner.reconcile('id', b, 'v1')
            self.assertEqual((b.queries, b.effects, runner.inspect('id')), before)

    def test_payload_and_version_stay_immutable_before_tool_access(self):
        for cls in (RecoverySDK, OrdinaryScript):
            runner = self.runner(cls)
            tool = self.tool('a', file=cls.__name__+'.db')
            runner.run('id', tool, {'x': 1}, 'v1')
            before = (tool.queries, tool.effects, runner.inspect('id'))
            with self.assertRaises(ValueError):
                runner.run('id', tool, {'x': 2}, 'v1')
            self.assertEqual(runner.run('id', tool, {'x': 1}, 'v2')['state'], 'VERSION_MISMATCH')
            self.assertEqual(runner.reconcile('id', tool, 'v2')['state'], 'VERSION_MISMATCH')
            self.assertEqual((tool.queries, tool.effects, runner.inspect('id')), before)

    def test_shared_receipt_database_partitions_accounts(self):
        a, b = self.tool('a'), self.tool('b')
        a.execute('id', {'x': 1})
        b.execute('id', {'x': 2})
        self.assertEqual(a.query('id'), [{'x': 1}])
        self.assertEqual(b.query('id'), [{'x': 2}])

    def test_append_wrong_account_preserves_unknown(self):
        runner = self.runner()
        a, b = self.tool('a', kind='append'), self.tool('b', kind='append')
        a.query_available = False
        runner.run('id', a, {}, 'v1', 'timeout_after')
        with self.assertRaises(ValueError):
            runner.run('id', b, {}, 'v1')
        self.assertEqual((b.queries, b.effects), (0, 0))
        self.assertEqual(runner.inspect('id')['state'], 'UNKNOWN')
        a.query_available = True
        self.assertEqual(runner.reconcile('id', a, 'v1')['state'], 'COMPLETED')

    def test_cached_completed_and_manual_review_reject_wrong_account(self):
        for kind, fault, state in [('ledger', None, 'COMPLETED'),
                                   ('append', 'timeout_before', 'MANUAL_REVIEW')]:
            runner = self.runner()
            a = self.tool('a', kind=kind, file=kind+'.db')
            b = self.tool('b', kind=kind, file=kind+'.db')
            self.assertEqual(runner.run(kind, a, {}, 'v1', fault)['state'], state)
            before = runner.inspect(kind)
            with self.assertRaises(ValueError):
                runner.run(kind, b, {}, 'v1')
            with self.assertRaises(ValueError):
                runner.reconcile(kind, b, 'v1')
            self.assertEqual((b.queries, b.effects), (0, 0))
            self.assertEqual(runner.inspect(kind), before)

    def test_legacy_identity_fails_closed_without_migration(self):
        runner = self.runner()
        tool = self.tool('a')
        with runner.db:
            runner.db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                              ('old', 'tool:ledger', '{}', 'v1', 'UNKNOWN', ''))
        before = runner.inspect('old')
        with self.assertRaises(ValueError):
            runner.run('old', tool, {}, 'v1')
        with self.assertRaises(ValueError):
            runner.reconcile('old', tool, 'v1')
        self.assertEqual((tool.queries, tool.effects), (0, 0))
        self.assertEqual(runner.inspect('old'), before)

    def test_same_label_on_different_database_is_not_authentication(self):
        runner = self.runner()
        a, b = self.tool('same', file='a.db'), self.tool('same', file='b.db')
        a.query_available = False
        runner.run('id', a, {}, 'v1', 'timeout_before')
        b.execute('id', {})
        self.assertEqual(runner.reconcile('id', b, 'v1')['state'], 'COMPLETED')
        a.query_available = True
        self.assertEqual(a.query('id'), [])  # Deliberate trust-limit counterexample.


if __name__ == '__main__':
    unittest.main()
