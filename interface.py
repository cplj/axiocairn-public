"""Shared JSON request contract for the CLI and local HTTP API."""
import json
import sqlite3
from .limits import check_size
from .recover_existing import recover

FIELDS = {'intent_db', 'receipt_db', 'name', 'kind', 'account', 'action', 'version'}


def decode_json(raw):
    check_size(raw)
    def reject_constant(value):
        raise ValueError('nonfinite JSON number: ' + value)

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field: ' + key)
            result[key] = value
        return result

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=object_pairs)


def error_result(error):
    return {'state': 'ERROR', 'error': type(error).__name__, 'message': str(error)}


def recover_request(request):
    """Return (JSON result, HTTP status); never execute an external action."""
    try:
        if not isinstance(request, dict) or set(request) != FIELDS:
            raise ValueError('expected exactly these fields: ' + ', '.join(sorted(FIELDS)))
        for key, value in request.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(key + ' must be a nonempty string')
        return recover(**request), 200
    except (ValueError, KeyError) as error:
        return error_result(error), 400
    except sqlite3.Error as error:
        return error_result(error), 422
