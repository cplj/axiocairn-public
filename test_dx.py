"""Entry-point regressions; the core recovery decisions are unchanged."""
import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from axiocairn import cli


class DXTests(unittest.TestCase):
    def call(self, argv, raw=''):
        output = io.StringIO()
        with redirect_stdout(output), patch('sys.stdin', io.StringIO(raw)):
            code = cli.main(argv)
        return code, output.getvalue()

    def test_help_explains_states_exits_and_receipt_directory(self):
        for argv in (['--help'], ['verify', '--help']):
            with self.assertRaises(SystemExit) as caught:
                _, output = self.call(argv)
            self.assertEqual(caught.exception.code, 0)
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            cli.main(['verify', '--help'])
        for word in ('intent.json', 'report.json', 'object.bin', '0 =', '2 =', '3 ='):
            self.assertIn(word, output.getvalue())

    def test_invalid_requests_keep_envelope_and_offer_original_input(self):
        for raw in ('', '{', '{}', '[]'):
            with self.subTest(raw=raw):
                code, output = self.call([], raw)
                result = json.loads(output)
                self.assertEqual(code, 2)
                self.assertEqual(set(result), {'state', 'error', 'message'})
                for word in ('stdin', 'docs/schemas/request.json', '--request', 'do not resend'):
                    self.assertIn(word, result['message'])

    def test_missing_files_point_to_original_evidence(self):
        for argv, expected in ((['--request', '/nonexistent/request.json'], 'docs/schemas/request.json'),
                               (['verify', '--receipts', '/nonexistent/receipts'], 'intent.json')):
            code, output = self.call(argv)
            self.assertEqual(code, 2)
            result = json.loads(output)
            self.assertEqual(result['state'], 'ERROR')
            self.assertIn(expected, result['message'])
            self.assertIn('/nonexistent/', result['message'])
            self.assertIn('do not resend', result['message'])

    def test_missing_prerequisites_are_reported_before_any_worker(self):
        spec = importlib.util.spec_from_file_location('local_runner', Path(__file__).parent / 'examples/run_local.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        args = SimpleNamespace(runtime='all', temporal_bin='temporal', inngest_bin='inngest',
                               restate_bin='restate-server', check=False)
        with patch.object(runner.shutil, 'which', return_value=None), \
             patch.object(runner.importlib.util, 'find_spec', return_value=None), \
             patch.object(runner.Processes, 'start') as start:
            with self.assertRaises(ValueError) as caught:
                asyncio.run(runner.main(args))
        start.assert_not_called()
        for value in ('--temporal-bin', '--inngest-bin', '--restate-bin', 'examples/requirements.txt', 'README.md#install'):
            self.assertIn(value, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
