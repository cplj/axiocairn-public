"""Recover an existing operation from a JSON request."""
import argparse
import json
import shlex
import sys
from .limits import MAX_JSON, read_bounded, read_file
from . import __version__
from .interface import decode_json, error_result, recover_request


EXITS = 'Exit codes: 0 = COMPLETED (verify also requires verified=true); 3 = unresolved state, no retry clearance; 2 = ERROR or invalid arguments.'


def explain_error(result, source, verify=False):
    if result['state'] == 'ERROR' and 'message' in result:
        if verify:
            hint = (f'Evidence directory: {source}. Restore the original intent.json, report.json and '
                    'object.bin snapshot (required for COMPLETED); see examples/receipt-bundle.schema.json. '
                    'Then run axiocairn verify --receipts ' + shlex.quote(source) + '.')
        else:
            hint = (f'Request input: {source}. Use the original seven-field request and existing intent_db/receipt_db; '
                    'see docs/schemas/request.json and README.md#input-and-output. '
                    'Run axiocairn --request /path/to/original/request.json after correcting the input.')
        result['message'] += ' ' + hint + ' Preserve the original operation and evidence; do not resend the action.'
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'verify':
        from .verify import verify_receipts
        parser = argparse.ArgumentParser(prog='axiocairn verify', description='Verify an exported receipt bundle offline', epilog=EXITS)
        parser.add_argument('--receipts', required=True, metavar='DIR',
                            help='original bundle directory: intent.json, report.json, and object.bin for COMPLETED; no network calls')
        args = parser.parse_args(argv[1:])
        try:
            result = verify_receipts(args.receipts)
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
            result = error_result(error)
        explain_error(result, args.receipts, verify=True)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result.get('verified') is True and result['state'] == 'COMPLETED' else (2 if result['state'] == 'ERROR' else 3)
    parser = argparse.ArgumentParser(description=__doc__, epilog=EXITS + ' Offline subcommand: axiocairn verify --receipts DIR (see verify --help).')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--request', default='-', metavar='FILE',
                        help='JSON request file (default: standard input); offline: axiocairn verify --receipts DIR')
    args = parser.parse_args(argv)
    try:
        raw = read_bounded(sys.stdin, MAX_JSON) if args.request == '-' else read_file(args.request, MAX_JSON).decode('utf-8')
        result, status = recover_request(decode_json(raw))
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        result, status = error_result(error), 400
    except Exception:
        result, status = error_result(RuntimeError('internal recovery error; inspect local stores')), 500
    explain_error(result, 'standard input (stdin)' if args.request == '-' else args.request)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    if status != 200:
        return 2
    return 0 if result['state'] == 'COMPLETED' else 3


if __name__ == '__main__':
    raise SystemExit(main())
