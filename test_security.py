"""Synthetic regression checks for HTTP boundaries, input limits and build policy."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

from axiocairn.file_service import ObjectHandler
from axiocairn.file_tool import HTTPFileTool, file_payload, run_file_operation
from axiocairn.interface import decode_json
from axiocairn.limits import MAX_JSON, MAX_OBJECT, MAX_RECEIPTS, InputLimitError, read_bounded
from axiocairn.local_http import LocalOpener
from axiocairn.sdk import RecoverySDK, SimulatedTool, canonical
from axiocairn.verify import verify_receipts


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.write_bytes(b'caller bytes')

    def server(self, handler, host='127.0.0.1'):
        server = HTTPServer((host, 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        return server

    def test_redirects_never_reach_second_service_or_export_bytes(self):
        hits = []
        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'synthetic internal response')
            def log_message(self, *args):
                pass
        target = self.server(Target)
        location = f'http://127.0.0.1:{target.server_port}/internal'
        code = 302
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(code)
                self.send_header('Location', location)
                self.end_headers()
            do_PUT = do_GET
            def log_message(self, *args):
                pass
        redirect = self.server(Redirect)
        url = f'http://127.0.0.1:{redirect.server_port}'
        tool = HTTPFileTool(url, self.source)
        sdk = RecoverySDK(self.root / 'intent.db')
        self.addCleanup(sdk.db.close)
        payload = file_payload('op', self.source.read_bytes())
        with sdk.db:
            sdk.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
                           ('op', tool.identity, canonical(payload), 'v1', 'UNKNOWN', ''))
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                bundle = self.root / str(code)
                request = dict(operation='op', payload=payload, source=str(self.source),
                               object_url=url, intent_db=str(self.root / 'intent.db'),
                               version='v1', receipts=str(bundle), reconcile_only=True)
                self.assertEqual(run_file_operation(request)['state'], 'UNKNOWN')
                self.assertFalse((bundle / 'object.bin').exists())
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    tool.execute('op', payload)
                caught.exception.close()
                self.assertIsNone(tool.observed)
        self.assertEqual(hits, [])

    def test_host_allowlist_rejects_private_linklocal_metadata_and_aliases(self):
        for host in ('10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254',
                     '169.254.170.2', 'metadata.google.internal', 'localhost',
                     '127.0.0.2', '2130706433', '0x7f000001', '[::1]', '[fd00:ec2::254]',
                     'example.com', '127.0.0.1.example.com', '127.0.0.1@10.0.0.1'):
            with self.subTest(host=host), patch('socket.getaddrinfo') as dns:
                with self.assertRaises(ValueError):
                    LocalOpener().open(f'http://{host}:8123/test')
                with self.assertRaises(ValueError):
                    HTTPFileTool(f'http://{host}:8123', self.source)
                dns.assert_not_called()

    def test_input_byte_boundaries_and_bounded_reads(self):
        self.assertEqual(decode_json('"' + 'x' * (MAX_JSON - 2) + '"'), 'x' * (MAX_JSON - 2))
        for raw in ('"' + 'x' * (MAX_JSON - 1) + '"', '"' + 'é' * (MAX_JSON // 2) + '"'):
            with self.assertRaisesRegex(InputLimitError, 'bytes'):
                decode_json(raw)
        with self.assertRaises(InputLimitError):
            canonical({'large': 'x' * MAX_JSON})
        stream = io.BytesIO(b'x' * (MAX_JSON + 100))
        with self.assertRaises(InputLimitError):
            read_bounded(stream, MAX_JSON)
        self.assertEqual(stream.tell(), MAX_JSON + 1)

    def test_cli_stdin_and_file_return_explicit_limit_error(self):
        self.source.write_bytes(b'x' * (MAX_JSON + 1))
        for args, body in (([], self.source.read_bytes()), (['--request', str(self.source)], None)):
            result = subprocess.run([sys.executable, '-m', 'axiocairn.cli', *args],
                                    input=body, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            output = json.loads(result.stdout)
            self.assertEqual(output['error'], 'InputLimitError')
            self.assertIn(str(MAX_JSON), output['message'])
            self.assertEqual(result.stderr, b'')

    def test_receipt_count_and_size_stop_without_replay(self):
        sdk = RecoverySDK(self.root / 'intent.db')
        tool = SimulatedTool(self.root / 'receipts.db', 'test', 'append', 'local')
        self.addCleanup(sdk.db.close)
        self.addCleanup(tool.db.close)
        sdk.run('op', tool, {}, 'v1')
        with tool.db:
            tool.db.execute('DELETE FROM receipts')
            tool.db.executemany('INSERT INTO receipts VALUES(?,?,?)',
                                [(tool.identity, 'op', '{}')] * MAX_RECEIPTS)
        self.assertEqual(len(tool.query('op')), MAX_RECEIPTS)
        with tool.db:
            tool.db.execute('INSERT INTO receipts VALUES(?,?,?)', (tool.identity, 'op', '{}'))
        with self.assertRaisesRegex(InputLimitError, 'count'):
            tool.query('op')
        report = sdk.reconcile('op', tool, 'v1')
        self.assertEqual(report['state'], 'MANUAL_REVIEW')
        self.assertIn('count exceeds', report['detail'])
        with tool.db:
            tool.db.execute('DELETE FROM receipts')
            tool.db.execute('INSERT INTO receipts VALUES(?,?,?)',
                            (tool.identity, 'op', 'x' * (MAX_JSON + 1)))
        with self.assertRaisesRegex(InputLimitError, 'bytes'):
            tool.query('op')
        with patch.object(tool, 'execute', side_effect=AssertionError('no replay')):
            self.assertEqual(sdk.run('op', tool, {}, 'v1')['state'], 'MANUAL_REVIEW')

    def test_source_and_bundle_files_have_size_bounds(self):
        with self.source.open('wb') as stream:
            stream.truncate(MAX_OBJECT + 1)
        tool = HTTPFileTool('http://127.0.0.1:8123', self.source)
        with patch.object(tool.opener, 'open') as opened:
            with self.assertRaisesRegex(InputLimitError, str(MAX_OBJECT)):
                tool.execute('op', {})
            opened.assert_not_called()
        bundle = self.root / 'bundle'
        bundle.mkdir()
        for name in ('intent.json', 'report.json'):
            (bundle / name).write_bytes(b'x' * (MAX_JSON + 1))
        with self.assertRaises(InputLimitError):
            verify_receipts(bundle)

    def test_aggregate_receipt_bytes_and_stored_intent_are_bounded(self):
        sdk = RecoverySDK(self.root / 'intent.db')
        tool = SimulatedTool(self.root / 'receipts.db', 'test', 'append', 'local')
        self.addCleanup(sdk.db.close)
        self.addCleanup(tool.db.close)
        sdk.run('op', tool, {}, 'v1')
        with tool.db:
            tool.db.execute('DELETE FROM receipts')
            tool.db.executemany('INSERT INTO receipts VALUES(?,?,?)',
                                [(tool.identity, 'op', json.dumps('x' * 60000))] * 18)
        with self.assertRaisesRegex(InputLimitError, 'total exceeds'):
            tool.query('op')
        with sdk.db:
            sdk.db.execute('UPDATE tasks SET payload=?', ('x' * (MAX_JSON + 1),))
        with self.assertRaisesRegex(InputLimitError, 'stored intent'):
            sdk.status('op')

    def test_object_exact_boundary_and_oversize_offline_snapshot(self):
        server = self.server(ObjectHandler)
        objects = self.root / 'objects'
        objects.mkdir()
        server.root = objects
        with self.source.open('wb') as stream:
            stream.truncate(MAX_OBJECT)
        request = dict(operation='op', version='v1', source=str(self.source),
                       payload=file_payload('op', self.source.read_bytes()),
                       object_url=f'http://127.0.0.1:{server.server_port}',
                       intent_db=str(self.root / 'intent.db'), receipts=str(self.root / 'bundle'))
        self.assertEqual(run_file_operation(request)['state'], 'COMPLETED')
        self.assertTrue(verify_receipts(request['receipts'])['verified'])
        with (Path(request['receipts']) / 'object.bin').open('ab') as stream:
            stream.write(b'x')
        with self.assertRaises(InputLimitError):
            verify_receipts(request['receipts'])

    def test_oversize_http_body_without_content_length_is_bounded(self):
        class Large(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(257):
                        self.wfile.write(b'x' * 65536)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        server = self.server(Large)
        tool = HTTPFileTool(f'http://127.0.0.1:{server.server_port}', self.source)
        with self.assertRaises(InputLimitError):
            tool.query('op')
        self.assertIsNone(tool.observed)

    def test_object_service_rejects_oversize_put_and_stored_get(self):
        server = self.server(ObjectHandler)
        server.root = self.root
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request('PUT', '/objects/op', body=b'', headers={'Content-Length': str(MAX_OBJECT + 1)})
        response = conn.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        self.assertFalse((self.root / 'op').exists())
        with (self.root / 'op').open('wb') as stream:
            stream.truncate(MAX_OBJECT + 1)
        conn.request('GET', '/objects/op')
        response = conn.getresponse()
        self.assertEqual(response.status, 413)
        response.read()

    def test_example_http_helper_enforces_same_target_policy(self):
        path = Path(__file__).parent / 'examples' / 'run_local.py'
        spec = importlib.util.spec_from_file_location('local_example', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module.http('http://169.254.169.254/latest/meta-data/')


class PackagingSecurityTests(unittest.TestCase):
    def test_workflow_actions_are_full_commit_shas_and_credentials_not_persisted(self):
        root = Path(__file__).parent
        workflows = [p for p in root.rglob('*') if p.is_file() and p.suffix in ('.yml', '.yaml')
                     and any(part in {'ci', '_pending_workflows', '.github'} for part in p.parts)]
        self.assertTrue(workflows)
        for path in workflows:
            data = path.read_text()
            for action in re.findall(r'uses:\s*([^\s#]+)', data):
                with self.subTest(path=str(path), action=action):
                    self.assertRegex(action, r'^[\w.-]+/[\w./-]+@[a-f0-9]{40}$')
            if 'actions/checkout@' in data:
                self.assertIn('persist-credentials: false', data)
            self.assertIn('permissions:', data)

    def test_image_is_pinned_and_runs_as_nonroot(self):
        data = (Path(__file__).parent / 'Dockerfile').read_text()
        self.assertRegex(data, r'(?m)^FROM python:3\.14\.8-bookworm@sha256:[a-f0-9]{64}$')
        self.assertRegex(data, r'(?m)^USER 65532:65532$')

    def test_public_security_text_describes_actual_limits(self):
        root = Path(__file__).parent
        self.assertNotIn('RC-SEC-01', (root / 'README.md').read_text())
        security = root / 'SECURITY.md'
        if security.exists():
            data = security.read_text()
            self.assertNotIn('RC-SEC-01', data)
            self.assertIn('redirect', data.lower())
            self.assertIn('trusted', data.lower())
