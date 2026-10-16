"""Dependency-free wheel backend for this fixed, pure-Python package."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Axiocairn requires Python 3.11 or newer; use a Python 3.11+ interpreter.")

import base64
import csv
import hashlib
import io
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).parent


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    name, version = project['name'], project['version']
    info = f'{name}-{version}.dist-info'
    files = {f'{name}/{module}.py': (ROOT / f'{module}.py').read_bytes()
             for module in ('__init__', 'sdk', 'recover_existing', 'interface', 'cli', 'api',
                            'file_tool', 'file_service', 'verify', 'limits', 'local_http')}
    metadata = (f'Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n'
                f'Summary: {project["description"]}\nRequires-Python: {project["requires-python"]}\n'
                'License-Expression: Apache-2.0\nLicense-File: LICENSE\nLicense-File: NOTICE\n'
                'Author: Axiocairn Authors\nDescription-Content-Type: text/markdown\n\n' + (ROOT / 'README.md').read_text())
    files[f'{info}/METADATA'] = metadata.encode()
    for document in ('LICENSE', 'NOTICE'):
        files[f'{info}/licenses/{document}'] = (ROOT / document).read_bytes()
    files[f'{info}/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: axiocairn-local\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    entry = project['scripts']['axiocairn']
    files[f'{info}/entry_points.txt'] = f'[console_scripts]\naxiocairn = {entry}\n'.encode()
    record = io.StringIO(newline='')
    writer = csv.writer(record)
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((path, 'sha256=' + digest, len(data)))
    writer.writerow((f'{info}/RECORD', '', ''))
    files[f'{info}/RECORD'] = record.getvalue().encode()
    filename = f'{name}-{version}-py3-none-any.whl'
    with zipfile.ZipFile(Path(wheel_directory) / filename, 'w', zipfile.ZIP_DEFLATED) as wheel:
        for path, data in files.items():
            wheel.writestr(path, data)
    return filename
