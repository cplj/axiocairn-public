"""File-object adapter: an actual HTTP PUT, followed by an independent GET."""
import hashlib
import http.client
from pathlib import Path
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .sdk import RecoverySDK, canonical
from .limits import MAX_OBJECT, read_bounded, read_file
from .local_http import LocalOpener, validate_url


def operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('operation must contain 1-100 letters, digits, underscores or hyphens')
    return value


def file_payload(action, content):
    return {'object': operation_id(action), 'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}


class HTTPFileTool:
    """Local development object store; the store, not the runtime, owns the effect.

    Empty evidence never authorizes replay. No credentials or cloud endpoints.
    """
    idempotent = False

    def __init__(self, url, source, query_unavailable=False):
        parts = validate_url(url)
        if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port
                or parts.path or parts.query or parts.fragment or parts.username or parts.password):
            raise ValueError('object URL must be http://127.0.0.1:PORT')
        self.url, self.source = url, Path(source)
        self.query_unavailable = query_unavailable
        self.observed = None
        self.opener = LocalOpener()

    @property
    def identity(self):
        return canonical(['http-file-object', self.url])

    def execute(self, action, payload, fault=None):
        operation_id(action)
        content = read_file(self.source, MAX_OBJECT)
        if file_payload(action, content) != payload:
            raise ValueError('source changed since intent was prepared')
        request = urllib.request.Request(self.url + '/objects/' + action, data=content, method='PUT',
                                         headers={'X-Ack-Loss': '1' if fault == 'timeout_after' else '0'})
        try:
            with self.opener.open(request, timeout=5) as response:
                read_bounded(response, MAX_OBJECT)
        except (TimeoutError, ConnectionError, http.client.RemoteDisconnected, urllib.error.URLError) as error:
            # HTTP 4xx/5xx is an execution error, not successful reconciliation.
            if isinstance(error, urllib.error.HTTPError):
                error.close()
                raise
            raise TimeoutError('PUT acknowledgement unavailable; query original operation') from error

    def query(self, action):
        self.observed = None
        request = urllib.request.Request(self.url + '/objects/' + operation_id(action),
                      headers={'X-Query-Unavailable': '1' if self.query_unavailable else '0'})
        try:
            with self.opener.open(request, timeout=5) as response:
                self.observed = read_bounded(response, MAX_OBJECT)
            return [file_payload(action, self.observed)]
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code == 404:
                return []
            raise ConnectionError('object query unavailable') from error
        except (TimeoutError, ConnectionError, urllib.error.URLError, http.client.HTTPException) as error:
            raise ConnectionError('object query unavailable') from error


def run_file_operation(request):
    """Worker entry point. Each call opens/closes its own local intent connection.

    Convert execution errors to ERROR results so durable runtimes do not retry a
    side effect on an adapter exception. Recovery itself never executes a PUT.
    """
    sdk = None
    try:
        action = operation_id(request['operation'])
        payload = request['payload']
        tool = HTTPFileTool(request['object_url'], request['source'], request.get('query_unavailable', False))
        sdk = RecoverySDK(request['intent_db'])
        if request.get('reconcile_only', False):
            if sdk.status(action)['payload'] != canonical(payload):
                raise ValueError('immutable intent conflict')
            report = sdk.reconcile(action, tool, request['version'])
        else:
            report = sdk.run(action, tool, payload, request['version'],
                             'timeout_after' if request.get('ack_loss', False) else None)
            # A stored COMPLETED must still have current evidence for export.
            if report['state'] == 'COMPLETED' and tool.observed is None:
                report = sdk.reconcile(action, tool, request['version'])
        bundle = Path(request['receipts'])
        bundle.mkdir(parents=True, exist_ok=False)
        intent = {'schema': 1, 'id': action, 'version': request['version'],
                  'tool': tool.identity, 'payload': canonical(payload)}
        (bundle / 'intent.json').write_text(canonical(intent) + '\n', encoding='utf-8')
        (bundle / 'report.json').write_text(canonical(report) + '\n', encoding='utf-8')
        if tool.observed is not None:
            (bundle / 'object.bin').write_bytes(tool.observed)
        return report
    except Exception as error:
        return {'state': 'ERROR', 'error': type(error).__name__, 'message': str(error)}
    finally:
        if sdk is not None:
            sdk.db.close()
