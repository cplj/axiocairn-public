"""Build-backend minimum-version diagnostic."""
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


class BuildTests(unittest.TestCase):
    def test_old_python_gets_explicit_version_error_before_tomllib_import(self):
        with patch('sys.version_info', (3, 9, 6)):
            with self.assertRaisesRegex(SystemExit, 'Axiocairn requires Python 3.11 or newer'):
                runpy.run_path(str(Path(__file__).with_name('_build.py')))
