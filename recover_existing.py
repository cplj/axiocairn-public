"""Recover local synthetic evidence using existing databases only."""
import argparse
import json
import sqlite3
import sys
from contextlib import ExitStack
from pathlib import Path
if __package__:
    from .sdk import RecoverySDK, SimulatedTool
else:
    from sdk import RecoverySDK, SimulatedTool


def open_existing(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)


class ExistingRecoverySDK(RecoverySDK):
    def __init__(self, path):
        self.db = open_existing(path)
        self.db.row_factory = sqlite3.Row


class ExistingSimulatedTool(SimulatedTool):
    def __init__(self, path, name, kind, account):
        if kind not in {'ledger', 'append'}:
            raise ValueError('unknown tool kind')
        if not isinstance(account, str) or not account.strip():
            raise ValueError('account scope required')
        self.name, self.kind, self.account = name, kind, account
        self.idempotent = kind == 'ledger'
        self.db = open_existing(path)
        self.query_available = True

    def query(self, action):
        try:
            return super().query(action)
        except sqlite3.OperationalError as error:
            code = getattr(error, 'sqlite_errorcode', 0) & 255
            if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise ConnectionError('receipt database locked; query unavailable') from error
            raise


def recover(intent_db, receipt_db, name, kind, account, action, version):
    for field, value in (('intent_db', intent_db), ('receipt_db', receipt_db)):
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError(field + ' must be nonempty')
    for field, value in (('name', name), ('account', account), ('action', action), ('version', version)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(field + ' must be a nonempty string')
    with ExitStack() as stack:
        runner = ExistingRecoverySDK(intent_db)
        stack.callback(runner.db.close)
        tool = ExistingSimulatedTool(receipt_db, name, kind, account)
        stack.callback(tool.db.close)
        return runner.reconcile(action, tool, version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('intent-db', 'receipt-db', 'name', 'account', 'action', 'version'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--kind', choices=('ledger', 'append'), required=True)
    args = parser.parse_args()
    try:
        result = recover(args.intent_db, args.receipt_db, args.name,
                         args.kind, args.account, args.action, args.version)
    except (sqlite3.Error, KeyError, ValueError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error),
                          'intent_db': args.intent_db, 'receipt_db': args.receipt_db}),
              file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
