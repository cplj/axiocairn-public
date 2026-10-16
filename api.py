"""Loopback-only, single-request-at-a-time receipt recovery HTTP service."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from .interface import decode_json, error_result, recover_request

MAX_BODY = 65536


class RecoveryHandler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == 501:
            code = 405
        self.respond(code, error_result(ValueError(message or 'unsupported HTTP request')))

    def respond(self, code, result):
        data = json.dumps(result, sort_keys=True, allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != '/recover':
            self.respond(404, error_result(ValueError('endpoint not found; use POST /recover')))
            return
        try:
            if self.headers.get_content_type() != 'application/json':
                self.respond(415, error_result(ValueError('Content-Type must be application/json')))
                return
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
                raise ValueError('one Content-Length required; Transfer-Encoding unsupported')
            length = int(lengths[0])
            if length <= 0:
                raise ValueError('request body must not be empty')
            if length > MAX_BODY:
                self.respond(413, error_result(ValueError('request exceeds 65536 bytes')))
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('incomplete request body')
            result, code = recover_request(decode_json(raw.decode('utf-8')))
        except (ValueError, UnicodeError, RecursionError) as error:
            result, code = error_result(error), 400
        except OSError as error:
            result, code = error_result(error), 408
        except Exception:
            result, code = error_result(RuntimeError('internal recovery error; inspect local stores')), 500
        self.respond(code, result)

    def do_GET(self):
        self.respond(405, error_result(ValueError('use POST /recover')))

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass  # Do not log local request paths or caller data.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765, help='loopback port (default: 8765)')
    args = parser.parse_args(argv)
    try:
        with HTTPServer(('127.0.0.1', args.port), RecoveryHandler) as server:
            print(f'Listening on http://127.0.0.1:{server.server_port}/recover', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (OSError, OverflowError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
