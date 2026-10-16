"""Offline inspection of an exported file-object receipt bundle. No network."""
import hashlib
from pathlib import Path

from .interface import decode_json
from .sdk import canonical
from .limits import MAX_JSON, MAX_OBJECT, read_file


def verify_receipts(directory):
    root = Path(directory)
    intent = decode_json(read_file(root / 'intent.json', MAX_JSON).decode('utf-8'))
    report = decode_json(read_file(root / 'report.json', MAX_JSON).decode('utf-8'))
    if not isinstance(intent, dict) or set(intent) != {'schema', 'id', 'version', 'tool', 'payload'}:
        raise ValueError('invalid intent schema')
    if type(intent['schema']) is not int or intent['schema'] != 1 or not isinstance(report, dict):
        raise ValueError('unsupported receipt schema')
    for field in ('id', 'version', 'tool', 'payload'):
        if not isinstance(intent[field], str) or not intent[field] or report.get(field) != intent[field]:
            raise ValueError('report/intent identity mismatch: ' + field)
    payload = decode_json(intent['payload'])
    if (not isinstance(payload, dict) or set(payload) != {'object', 'sha256', 'size'}
            or payload['object'] != intent['id'] or canonical(payload) != intent['payload']
            or type(payload['size']) is not int or not 0 <= payload['size'] <= MAX_OBJECT
            or not isinstance(payload['sha256'], str) or len(payload['sha256']) != 64):
        raise ValueError('invalid file-object intent')
    if report.get('state') != 'COMPLETED':
        return {'state': report.get('state', 'UNKNOWN'), 'verified': False,
                'detail': 'unresolved report; no success and no retry clearance'}
    content = read_file(root / 'object.bin', MAX_OBJECT)
    if len(content) != payload['size'] or hashlib.sha256(content).hexdigest() != payload['sha256']:
        raise ValueError('object snapshot does not match original intent')
    return {'state': 'COMPLETED', 'verified': True, 'id': intent['id'],
            'sha256': payload['sha256'], 'size': payload['size'],
            'detail': 'offline bundle consistency verified; origin and current server state are not authenticated'}
