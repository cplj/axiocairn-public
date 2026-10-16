"""Installed-package contract tests, using only synthetic temporary stores."""
import http.client
import importlib.metadata
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from axiocairn import RecoverySDK, SimulatedTool, __version__, recover
from axiocairn.api import HTTPServer, MAX_BODY, RecoveryHandler


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = dict(intent_db=str(self.root / 'intent.db'),
                            receipt_db=str(self.root / 'receipts.db'), name='payments',
                            kind='ledger', account='sandbox-a', action='action-1', version='v1')
        runner = RecoverySDK(self.request['intent_db'])
        tool = SimulatedTool(self.request['receipt_db'], 'payments', 'ledger', 'sandbox-a')
        try:
            tool.query_available = False
            self.assertEqual(runner.run('action-1', tool, {'amount': 1}, 'v1', 'timeout_after')['state'], 'UNKNOWN')
        finally:
            runner.db.close()
            tool.db.close()
        self.server = HTTPServer(('127.0.0.1', 0), RecoveryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def cli(self, request=None, raw=None, file=False):
        body = json.dumps(self.request if request is None else request) if raw is None else raw
        command = [sys.executable, '-B', '-m', 'axiocairn.cli']
        if file:
            path = self.root / 'request.json'
            path.write_text(body)
            command += ['--request', str(path)]
        proc = subprocess.run(command, input=body, text=True, capture_output=True,
                              cwd=self.root, timeout=15)
        self.assertEqual(proc.stderr, '')
        return proc.returncode, json.loads(proc.stdout)

    def api(self, request=None, raw=None, path='/recover', method='POST', headers=None):
        body = json.dumps(self.request if request is None else request).encode() if raw is None else raw
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
        try:
            conn.request(method, path, body=body,
                         headers={'Content-Type': 'application/json'} if headers is None else headers)
            response = conn.getresponse()
            self.assertEqual(response.getheader('Content-Type'), 'application/json; charset=utf-8')
            return response.getcode(), json.load(response)
        finally:
            conn.close()

    def effect_count(self):
        with sqlite3.connect(self.request['receipt_db']) as db:
            return db.execute('SELECT count(*) FROM receipts').fetchone()[0]

    def test_installed_version_and_console_help(self):
        self.assertEqual(__version__, '0.2.0')
        self.assertEqual(importlib.metadata.version('axiocairn'), '0.2.0')
        command = str(Path(sys.executable).parent / 'axiocairn')
        for args, expected in [(['--version'], '0.2.0'), (['--help'], '--request')]:
            proc = subprocess.run([command, *args], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(expected, proc.stdout)

    def test_cli_api_sdk_agree_and_never_execute(self):
        with patch.object(SimulatedTool, 'execute', side_effect=AssertionError('must never execute')):
            sdk = recover(**self.request)
            http_code, api = self.api()
        code, cli = self.cli(file=True)
        self.assertEqual((code, http_code, sdk['state']), (0, 200, 'COMPLETED'))
        self.assertEqual(cli, api)
        self.assertEqual(sdk, api)
        self.assertEqual(self.effect_count(), 1)

    def test_real_locked_receipt_stays_unknown_then_recovers(self):
        lock = sqlite3.connect(self.request['receipt_db'])
        self.addCleanup(lock.close)
        lock.execute('BEGIN EXCLUSIVE')
        try:
            code, cli = self.cli()
            http_code, api = self.api()
            self.assertEqual((code, http_code, cli['state']), (3, 200, 'UNKNOWN'))
            self.assertEqual(cli, api)
        finally:
            lock.rollback()
        self.assertEqual(recover(**self.request)['state'], 'COMPLETED')
        self.assertEqual(self.effect_count(), 1)

    def stored_task(self):
        with sqlite3.connect(self.request['intent_db']) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM tasks').fetchone())

    def test_completed_alternate_empty_store_agrees_and_preserves_original(self):
        for kind in ('ledger', 'append'):
            with self.subTest(kind=kind):
                self.request['kind'] = kind
                with sqlite3.connect(self.request['intent_db']) as db:
                    db.execute('UPDATE tasks SET tool=?',
                               (json.dumps(['payments', kind, 'sandbox-a'], separators=(',', ':')),))
                before = recover(**self.request)
                self.assertEqual(before['state'], 'COMPLETED')
                empty = self.root / (kind + '-empty.db')
                tool = SimulatedTool(empty, 'payments', kind, 'sandbox-a')
                tool.db.close()
                request = dict(self.request, receipt_db=str(empty))
                code, cli = self.cli(request)
                http_code, api = self.api(request)
                self.assertEqual((code, http_code, cli['state']), (3, 200, 'MANUAL_REVIEW'))
                self.assertEqual(cli, api)
                self.assertEqual(cli, recover(**request))
                self.assertEqual(self.stored_task(), before)
                self.assertEqual(recover(**self.request), before)
                # Rebind the original synthetic receipt for the next kind.
                with sqlite3.connect(self.request['receipt_db']) as db:
                    db.execute('UPDATE receipts SET name=?',
                               (json.dumps(['payments', 'append', 'sandbox-a'], separators=(',', ':')),))
                self.assertEqual(self.effect_count(), 1)

    def test_completed_locked_store_agrees_and_preserves_original(self):
        before = recover(**self.request)
        lock = sqlite3.connect(self.request['receipt_db'])
        self.addCleanup(lock.close)
        lock.execute('BEGIN EXCLUSIVE')
        try:
            code, cli = self.cli()
            http_code, api = self.api()
            self.assertEqual((code, http_code, cli['state']), (3, 200, 'MANUAL_REVIEW'))
            self.assertEqual(cli, api)
            self.assertEqual(cli, recover(**self.request))
            self.assertEqual(self.stored_task(), before)
        finally:
            lock.rollback()
        self.assertEqual(recover(**self.request), before)
        self.assertEqual(self.effect_count(), 1)

    def test_nonstring_and_deep_receipts_agree_as_manual_review(self):
        for payload in (None, b'{}', '[' * 100000 + '0' + ']' * 100000):
            with self.subTest(payload_type=type(payload).__name__):
                with sqlite3.connect(self.request['intent_db']) as db:
                    db.execute("UPDATE tasks SET state='UNKNOWN'")
                with sqlite3.connect(self.request['receipt_db']) as db:
                    db.execute('UPDATE receipts SET payload=?', (payload,))
                code, cli = self.cli()
                http_code, api = self.api()
                self.assertEqual((code, http_code, cli['state']), (3, 200, 'MANUAL_REVIEW'))
                self.assertEqual(cli, api)
                self.assertEqual(cli, recover(**self.request))
                self.assertEqual(self.stored_task()['state'], 'MANUAL_REVIEW')
                self.assertEqual(self.effect_count(), 1)

    def test_missing_stores_fail_without_creating_files(self):
        for key in ('intent_db', 'receipt_db'):
            missing = self.root / (key + '-missing.db')
            request = dict(self.request, **{key: str(missing)})
            code, cli = self.cli(request)
            http_code, api = self.api(request)
            self.assertEqual((code, http_code, cli['state']), (2, 422, 'ERROR'))
            self.assertEqual(set(cli), set(api))
            self.assertEqual((cli['state'], cli['error']), (api['state'], api['error']))
            self.assertTrue(cli['message'].startswith(api['message']))
            self.assertIn('docs/schemas/request.json', cli['message'])
            self.assertFalse(missing.exists())

    def test_missing_action_and_schema_are_errors(self):
        for request in (dict(self.request, action='missing'),):
            self.assertEqual(self.cli(request)[0], 2)
            self.assertEqual(self.api(request)[0], 400)
        empty = self.root / 'empty.db'
        sqlite3.connect(empty).close()
        request = dict(self.request, receipt_db=str(empty))
        self.assertEqual(self.api(request)[0], 422)
        self.assertEqual(self.cli(request)[0], 2)

    def test_account_version_and_stored_state_are_preserved(self):
        request = dict(self.request, account='wrong-account')
        self.assertEqual(self.cli(request)[0], 2)
        self.assertEqual(self.api(request)[0], 400)
        request = dict(self.request, version='v2')
        code, cli = self.cli(request)
        http_code, api = self.api(request)
        self.assertEqual((code, http_code, cli['state']), (3, 200, 'VERSION_MISMATCH'))
        self.assertEqual(cli, api)
        with sqlite3.connect(self.request['intent_db']) as db:
            self.assertEqual(db.execute('SELECT state FROM tasks').fetchone()[0], 'UNKNOWN')
        self.assertEqual(self.effect_count(), 1)

    def test_manual_review_is_sticky_after_receipt_repair(self):
        with sqlite3.connect(self.request['receipt_db']) as db:
            db.execute("UPDATE receipts SET payload='{broken'")
        code, cli = self.cli()
        self.assertEqual((code, cli['state']), (3, 'MANUAL_REVIEW'))
        with sqlite3.connect(self.request['receipt_db']) as db:
            db.execute('UPDATE receipts SET payload=?', ('{"amount":1}',))
        http_code, api = self.api()
        self.assertEqual(http_code, 200)
        self.assertEqual(cli, api)
        self.assertEqual(self.effect_count(), 1)

    def test_empty_ledger_receipt_reports_retry_safe_without_execution(self):
        with sqlite3.connect(self.request['receipt_db']) as db:
            db.execute('DELETE FROM receipts')
        code, cli = self.cli()
        http_code, api = self.api()
        self.assertEqual((code, http_code, cli['state']), (3, 200, 'RETRY_SAFE'))
        self.assertEqual(cli, api)
        self.assertEqual(self.effect_count(), 0)

    def test_invalid_json_and_fields_agree(self):
        for raw in ('{broken', '[]', '{}', '{"action":"a","action":"b"}',
                    '{"action":NaN}', ''):
            with self.subTest(raw=raw):
                self.assertEqual(self.cli(raw=raw)[0], 2)
                self.assertEqual(self.api(raw=raw.encode())[0], 400)
        for request in (dict(self.request, account=' '), dict(self.request, action=3),
                        dict(self.request, extra='x'), dict(self.request, kind='other')):
            code, cli = self.cli(request)
            http_code, api = self.api(request)
            self.assertEqual((code, http_code), (2, 400))
            self.assertEqual(set(cli), set(api))
            self.assertEqual((cli['state'], cli['error']), (api['state'], api['error']))
            self.assertTrue(cli['message'].startswith(api['message']))
            self.assertIn('docs/schemas/request.json', cli['message'])
        self.assertEqual(self.effect_count(), 1)

    def test_http_errors_are_json_and_body_limit_is_enforced(self):
        for kwargs, http_code in [({'path': '/missing'}, 404), ({'method': 'GET'}, 405),
                               ({'method': 'PUT'}, 405),
                               ({'headers': {'Content-Type': 'text/plain'}}, 415),
                               ({'raw': b'x' * (MAX_BODY + 1)}, 413),
                               ({'raw': b'\xff'}, 400),
                               ({'headers': {'Content-Type': 'application/json',
                                             'Transfer-Encoding': 'chunked'}}, 400)]:
            with self.subTest(kwargs=list(kwargs), http_code=http_code):
                observed, result = self.api(**kwargs)
                self.assertEqual(observed, http_code)
                self.assertEqual(result['state'], 'ERROR')

    def test_truncated_body_returns_json_error(self):
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(b'POST /recover HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{}')
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.getcode(), 400)
            self.assertEqual(json.load(response)['state'], 'ERROR')

    def test_unexpected_failure_is_not_reported_as_success(self):
        with patch('axiocairn.api.recover_request', side_effect=RuntimeError('failure')):
            http_code, result = self.api()
            self.assertEqual((http_code, result['state']), (500, 'ERROR'))
        self.assertEqual(self.effect_count(), 1)


if __name__ == '__main__':
    unittest.main()
