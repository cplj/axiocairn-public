"""Synthetic receipt failures with no external service or credentials."""
import json
import tempfile
from pathlib import Path
from sdk import RecoverySDK, SimulatedTool
from ordinary_script import OrdinaryScript


class FaultTool(SimulatedTool):
    def __init__(self, path, account='account-a'):
        super().__init__(path, 'ledger', 'ledger', account)
        self.queries = self.execute_calls = 0
        self.query_error = None

    def query(self, action):
        self.queries += 1
        if self.query_error is not None:
            raise self.query_error
        return super().query(action)

    def execute(self, *args):
        self.execute_calls += 1
        return super().execute(*args)


def scenario(runner_type, directory, failure):
    root = Path(directory)
    runner = runner_type(str(root / 'intent.db'))
    tool = FaultTool(str(root / 'tool.db'))
    try:
        tool.query_available = False
        initial = runner.run('action', tool, {'amount': 1}, 'v1', 'timeout_after')['state']
        tool.query_available = True
        if failure == 'timeout':
            tool.query_error = TimeoutError('receipt query timed out')
        else:
            raw = '{broken' if failure == 'malformed' else '{"amount":NaN}'
            with tool.db:
                tool.db.execute('UPDATE receipts SET payload=?', (raw,))
        stopped = runner.reconcile('action', tool, 'v1')['state']
        repeated = runner.run('action', tool, {'amount': 1}, 'v1')['state']
        queries_before_restore = tool.queries
        tool.query_error = None
        with tool.db:
            tool.db.execute('UPDATE receipts SET payload=?', ('{"amount":1}',))
        runner.db.close()
        runner = runner_type(str(root / 'intent.db'))
        restored = runner.reconcile('action', tool, 'v1')['state']
        reopened_run = runner.run('action', tool, {'amount': 1}, 'v1')['state']
        result = {'initial': initial, 'query_failure': stopped, 'repeated_run': repeated,
                  'after_receipt_restored_and_reopened': restored, 'reopened_run': reopened_run,
                  'queries_after_restore': tool.queries - queries_before_restore,
                  'execute_calls': tool.execute_calls,
                  'receipt_count': tool.db.execute('SELECT count(*) FROM receipts').fetchone()[0]}
        final = 'COMPLETED' if failure == 'timeout' else 'MANUAL_REVIEW'
        assert result == {'initial': 'UNKNOWN', 'query_failure': 'UNKNOWN' if failure == 'timeout' else 'MANUAL_REVIEW',
                          'repeated_run': 'UNKNOWN' if failure == 'timeout' else 'MANUAL_REVIEW',
                          'after_receipt_restored_and_reopened': final, 'reopened_run': final,
                          'queries_after_restore': 1 if failure == 'timeout' else 0,
                          'execute_calls': 1, 'receipt_count': 1}
        return result
    finally:
        runner.db.close()
        tool.db.close()


if __name__ == '__main__':
    results = {}
    for failure in ('timeout', 'malformed', 'nonfinite'):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            observed = scenario(RecoverySDK, a, failure)
            comparator = scenario(OrdinaryScript, b, failure)
            assert observed == comparator
            results[failure] = {'sdk': observed, 'ordinary_script': comparator, 'comparison': 'TIE'}
    print(json.dumps({'scope': 'local_synthetic', 'business_outcome': 'NOT_VERIFIED', 'cases': results}, indent=2))
