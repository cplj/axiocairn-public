"""Real HTTP object adapter and offline evidence failure-path tests."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer

from axiocairn.file_service import ObjectHandler
from axiocairn.file_tool import file_payload, run_file_operation
from axiocairn.verify import verify_receipts


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'caller.txt'
        self.source.write_bytes(b'caller-owned bytes\x00\xff\n')
        self.objects = self.root / 'external'
        self.objects.mkdir()
        self.server = HTTPServer(('127.0.0.1', 0), ObjectHandler)
        self.server.root = self.objects
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.request = {'operation': 'case-1', 'version': 'v1',
                        'source': str(self.source), 'payload': file_payload('case-1', self.source.read_bytes()),
                        'object_url': f'http://127.0.0.1:{self.server.server_port}',
                        'intent_db': str(self.root / 'intent.db'), 'receipts': str(self.root / 'receipts')}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def cli(self):
        result = subprocess.run([sys.executable, '-m', 'axiocairn.cli', 'verify', '--receipts',
                                 self.request['receipts']], capture_output=True, text=True, timeout=5)
        return result.returncode, json.loads(result.stdout)

    def test_normal_write_and_offline_verify(self):
        self.assertEqual(run_file_operation(self.request)['state'], 'COMPLETED')
        self.assertEqual((self.objects / 'case-1').read_bytes(), self.source.read_bytes())
        code, result = self.cli()
        self.assertEqual(code, 0)
        self.assertTrue(result['verified'])

    def test_lost_ack_reconciles_real_bytes_without_second_put(self):
        result = run_file_operation(dict(self.request, ack_loss=True))
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertTrue(verify_receipts(self.request['receipts'])['verified'])
        before = (self.objects / 'case-1').stat().st_mtime_ns
        again = dict(self.request, receipts=str(self.root / 'again'))
        self.assertEqual(run_file_operation(again)['state'], 'COMPLETED')
        self.assertEqual((self.objects / 'case-1').stat().st_mtime_ns, before)

    def test_unknown_does_not_succeed_or_reexecute(self):
        result = run_file_operation(dict(self.request, ack_loss=True, query_unavailable=True))
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertTrue((self.objects / 'case-1').is_file())
        self.assertEqual(self.cli()[0], 3)
        again = dict(self.request, reconcile_only=True, receipts=str(self.root / 'again'))
        self.assertEqual(run_file_operation(again)['state'], 'COMPLETED')
        self.assertEqual(len(list(self.objects.iterdir())), 1)

    def test_changed_external_object_requires_manual_review(self):
        run_file_operation(self.request)
        (self.objects / 'case-1').write_bytes(b'changed')
        again = dict(self.request, reconcile_only=True, receipts=str(self.root / 'again'))
        self.assertEqual(run_file_operation(again)['state'], 'MANUAL_REVIEW')
        self.assertFalse(verify_receipts(again['receipts'])['verified'])

    def test_snapshot_tamper_is_error(self):
        run_file_operation(self.request)
        (Path(self.request['receipts']) / 'object.bin').write_bytes(b'tampered')
        self.assertEqual(self.cli()[0], 2)

    def test_missing_snapshot_is_error(self):
        run_file_operation(self.request)
        snapshot = Path(self.request['receipts']) / 'object.bin'
        snapshot.rename(snapshot.with_suffix('.saved'))
        self.assertEqual(self.cli()[0], 2)

    def test_report_identity_tamper_is_error(self):
        run_file_operation(self.request)
        path = Path(self.request['receipts']) / 'report.json'
        report = json.loads(path.read_text())
        report['id'] = 'another-operation'
        path.write_text(json.dumps(report))
        self.assertEqual(self.cli()[0], 2)

    def test_duplicate_json_key_is_error(self):
        run_file_operation(self.request)
        path = Path(self.request['receipts']) / 'intent.json'
        path.write_text(path.read_text().replace('"schema":1', '"schema":1,"schema":1'))
        self.assertEqual(self.cli()[0], 2)

    def test_changed_source_is_error_and_does_not_create_object(self):
        self.source.write_bytes(b'changed after intent')
        self.assertEqual(run_file_operation(self.request)['state'], 'ERROR')
        self.assertFalse((self.objects / 'case-1').exists())

    def test_different_intent_conflict_never_overwrites_object(self):
        run_file_operation(self.request)
        original = (self.objects / 'case-1').read_bytes()
        self.source.write_bytes(b'new content')
        changed = dict(self.request, payload=file_payload('case-1', self.source.read_bytes()),
                       receipts=str(self.root / 'again'))
        self.assertEqual(run_file_operation(changed)['state'], 'ERROR')
        self.assertEqual((self.objects / 'case-1').read_bytes(), original)

    def test_version_mismatch_is_not_success(self):
        run_file_operation(self.request)
        changed = dict(self.request, version='v2', receipts=str(self.root / 'again'))
        self.assertEqual(run_file_operation(changed)['state'], 'VERSION_MISMATCH')
        with self.assertRaises(ValueError):
            verify_receipts(changed['receipts'])

    def test_existing_unknown_object_conflict_is_error(self):
        (self.objects / 'case-1').write_bytes(self.source.read_bytes())
        self.assertEqual(run_file_operation(self.request)['state'], 'ERROR')

    def test_bad_operation_cannot_escape_root(self):
        self.assertEqual(run_file_operation(dict(self.request, operation='../escape'))['state'], 'ERROR')

    def test_nonlocal_endpoint_is_error(self):
        self.assertEqual(run_file_operation(dict(self.request, object_url='https://example.com'))['state'], 'ERROR')

    def test_empty_receipts_is_error(self):
        self.assertEqual(self.cli()[0], 2)

    def test_reconcile_only_cannot_substitute_payload(self):
        run_file_operation(self.request)
        changed = dict(self.request, reconcile_only=True, payload=file_payload('case-1', b'different'),
                       receipts=str(self.root / 'again'))
        self.assertEqual(run_file_operation(changed)['state'], 'ERROR')
        self.assertEqual((self.objects / 'case-1').read_bytes(), self.source.read_bytes())
