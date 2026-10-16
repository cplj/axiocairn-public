"""Bounded generative contract tests, stdlib only. Reproduce with seed 20261015.

Each loop samples a defined domain and asserts an invariant or roundtrip, rather
than treating a lack of exceptions as success. No network or worker effects.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import random
import re
import tempfile
import unittest
from unittest.mock import patch

from axiocairn import cli
from axiocairn.file_tool import operation_id
from axiocairn.interface import FIELDS, decode_json, recover_request
from axiocairn.limits import MAX_JSON, InputLimitError, read_bounded
from axiocairn.local_http import validate_url
from axiocairn.sdk import RecoverySDK, SimulatedTool, canonical
from axiocairn.verify import verify_receipts

SEED = 20261015
CASES = 300


def value(rng, depth=0):
    leaves = [None, True, False, rng.randint(-10**18, 10**18), rng.uniform(-1e10, 1e10),
              ''.join(rng.choices('abc XYZ\\\"\n\t漢字🙂', k=rng.randrange(40)))]
    if depth < 4:
        choice = rng.randrange(3)
        if choice == 0:
            return [value(rng, depth + 1) for _ in range(rng.randrange(5))]
        if choice == 1:
            return {f'key-{n}': value(rng, depth + 1) for n in range(rng.randrange(5))}
    return rng.choice(leaves)


class PropertyTests(unittest.TestCase):
    def test_json_and_canonical_roundtrip(self):
        rng = random.Random(SEED)
        for _ in range(CASES):
            item = value(rng)
            self.assertEqual(decode_json(json.dumps(item, ensure_ascii=False)), item)
            self.assertEqual(decode_json(canonical(item)), item)
            self.assertEqual(canonical(decode_json(canonical(item))), canonical(item))

    def test_duplicate_keys_and_nonfinite_numbers_rejected_at_any_depth(self):
        rng = random.Random(SEED + 1)
        for _ in range(CASES):
            key = json.dumps(''.join(rng.choices('a漢🙂\\\"', k=rng.randrange(1, 20))))
            nesting = rng.randrange(50)
            raw = '[' * nesting + '{' + key + ':0,' + key + ':1}' + ']' * nesting
            with self.assertRaisesRegex(ValueError, 'duplicate JSON field'):
                decode_json(raw)
            raw = '[' * nesting + rng.choice(['NaN', 'Infinity', '-Infinity']) + ']' * nesting
            with self.assertRaisesRegex(ValueError, 'nonfinite'):
                decode_json(raw)

    def test_invalid_request_domains_never_reach_store_recovery(self):
        rng = random.Random(SEED + 2)
        fields = sorted(FIELDS)
        for n in range(CASES):
            request = dict.fromkeys(fields, 'original')
            key = rng.choice(fields)
            if n % 3 == 0:
                del request[key]
            elif n % 3 == 1:
                request[key] = rng.choice([None, False, 0, [], {}, '', ' \t\n'])
            else:
                request['unexpected-' + str(n)] = value(rng)
            with patch('axiocairn.interface.recover') as recover:
                result, status = recover_request(request)
            recover.assert_not_called()
            self.assertEqual((status, result['state']), (400, 'ERROR'))

    def test_bounded_stream_read_and_utf8_byte_limit(self):
        rng = random.Random(SEED + 3)
        for _ in range(CASES):
            limit = rng.randrange(1, 2048)
            size = rng.randrange(0, limit * 3)
            stream = io.BytesIO(b'x' * size)
            if size > limit:
                with self.assertRaises(InputLimitError):
                    read_bounded(stream, limit)
                self.assertEqual(stream.tell(), limit + 1)
            else:
                self.assertEqual(read_bounded(stream, limit), b'x' * size)
            raw = json.dumps('漢' * (MAX_JSON // 3 + rng.randrange(1, 20)), ensure_ascii=False)
            with self.assertRaises(InputLimitError):
                decode_json(raw)
        class HugeStream:
            def read(self, size):
                self.requested = size
                return b'x' * size
        huge = HugeStream()
        with self.assertRaises(InputLimitError):
            read_bounded(huge, MAX_JSON)
        self.assertEqual(huge.requested, MAX_JSON + 1)

    def test_operation_and_url_grammar_preserve_exact_scope(self):
        rng = random.Random(SEED + 4)
        for n in range(CASES):
            candidate = ''.join(rng.choices('ab19_-./漢\n', k=rng.randrange(110)))
            accepted = re.fullmatch(r'[A-Za-z0-9_-]{1,100}', candidate) is not None
            if accepted:
                self.assertEqual(operation_id(candidate), candidate)
            else:
                with self.assertRaises(ValueError):
                    operation_id(candidate)
            port = rng.randrange(1, 65536)
            good = f'http://127.0.0.1:{port}'
            self.assertEqual(validate_url(good).port, port)
            bad = rng.choice([f'https://127.0.0.1:{port}', f'http://localhost:{port}',
                              f'http://127.0.0.1:{port}#fragment', f'http://user@127.0.0.1:{port}',
                              f'http://10.0.0.{n % 256}:{port}'])
            with self.assertRaises(ValueError):
                validate_url(bad)

    def test_exported_bundle_roundtrip_and_tamper_never_success(self):
        rng = random.Random(SEED + 5)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n in range(CASES):
                content = rng.randbytes(rng.randrange(1, 2048))
                payload = {'object': f'op-{n}', 'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}
                intent = dict(schema=1, id=f'op-{n}', version='v1', tool='local-tool', payload=canonical(payload))
                report = dict(intent, state='COMPLETED')
                (root / 'intent.json').write_text(canonical(intent))
                (root / 'report.json').write_text(canonical(report))
                (root / 'object.bin').write_bytes(content)
                self.assertTrue(verify_receipts(root)['verified'])
                (root / 'object.bin').write_bytes(content + b'!')
                with self.assertRaisesRegex(ValueError, 'snapshot'):
                    verify_receipts(root)
                report['state'] = rng.choice(['UNKNOWN', 'MANUAL_REVIEW', 'VERSION_MISMATCH', 'PREPARED', 'RETRY_SAFE', 'ERROR'])
                (root / 'report.json').write_text(canonical(report))
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli.main(['verify', '--receipts', directory])
                self.assertEqual(code, 2 if report['state'] == 'ERROR' else 3)
                self.assertFalse(json.loads(output.getvalue())['verified'])

    def test_malformed_large_and_deep_cli_input_is_error_exit_without_effect(self):
        rng = random.Random(SEED + 6)
        inputs = ['x' * (8 * 1024 * 1024), '[' * 2000 + '0' + ']' * 2000, '\ud800', '', '{']
        inputs += ['!' + ''.join(rng.choices('abc{}[]漢🙂', k=rng.randrange(200))) for _ in range(CASES)]
        for raw in inputs:
            output = io.StringIO()
            with patch('sys.stdin', io.StringIO(raw)), redirect_stdout(output), \
                 patch('axiocairn.interface.recover') as recover:
                code = cli.main([])
            recover.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual((code, result['state']), (2, 'ERROR'))
            self.assertIn('docs/schemas/request.json', result['message'])

    def test_generated_receipts_reconcile_without_execution_and_keep_manual_stop(self):
        rng = random.Random(SEED + 7)
        with tempfile.TemporaryDirectory() as directory:
            sdk = RecoverySDK(Path(directory) / 'intent.db')
            tool = SimulatedTool(Path(directory) / 'receipt.db', 'ledger', 'ledger', 'local')
            try:
                with patch.object(tool, 'execute', side_effect=AssertionError('recovery must never execute')):
                    for n in range(CASES):
                        action = f'op-{n}'
                        payload = value(rng)
                        with sdk.db:
                            sdk.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
                                           (action, tool.identity, canonical(payload), 'v1', 'UNKNOWN', ''))
                        with tool.db:
                            tool.db.execute('INSERT INTO receipts VALUES(?,?,?)',
                                            (tool.identity, action, canonical(payload)))
                        self.assertEqual(sdk.reconcile(action, tool, 'v1')['state'], 'COMPLETED')
                        malformed = rng.choice(['{broken', 'NaN', '[' * 2000 + '0' + ']' * 2000, 'x' * (MAX_JSON + 1)])
                        with tool.db:
                            tool.db.execute('UPDATE receipts SET payload=? WHERE action=?', (malformed, action))
                        self.assertEqual(sdk.reconcile(action, tool, 'v1')['state'], 'MANUAL_REVIEW')
                        with tool.db:
                            tool.db.execute('UPDATE receipts SET payload=? WHERE action=?', (canonical(payload), action))
                        self.assertEqual(sdk.reconcile(action, tool, 'v1')['state'], 'MANUAL_REVIEW')
            finally:
                sdk.db.close()
                tool.db.close()


if __name__ == '__main__':
    print(f'property seed={SEED}; 8 properties; {CASES} generated cases per property + 5 pinned boundaries', flush=True)
    unittest.main(verbosity=2)
