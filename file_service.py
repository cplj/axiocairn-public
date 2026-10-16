"""Loopback file-object dev server. Data is real; faults are explicitly injected."""
import argparse
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket

from .file_tool import operation_id
from .limits import MAX_OBJECT, InputLimitError, read_file


class ObjectHandler(BaseHTTPRequestHandler):
    def object_path(self):
        if not self.path.startswith('/objects/'):
            raise ValueError('use /objects/OPERATION')
        return self.server.root / operation_id(self.path[len('/objects/'):])

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            return
        try:
            path = self.object_path()
        except ValueError:
            self.send_error(400)
            return
        if self.headers.get('X-Query-Unavailable') == '1':
            self.send_error(503)
            return
        try:
            content = read_file(path, MAX_OBJECT)
        except InputLimitError as error:
            self.send_error(413, str(error))
            return
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_PUT(self):
        try:
            path = self.object_path()
            length = int(self.headers['Content-Length'])
            if self.headers.get('Transfer-Encoding') or length < 0:
                raise ValueError('unsupported transfer encoding or negative length')
            if length > MAX_OBJECT:
                self.send_error(413, f'object exceeds {MAX_OBJECT} bytes')
                return
            self.connection.settimeout(5)
            content = self.rfile.read(length)
            if len(content) != length:
                raise ValueError('incomplete body')
        except (ValueError, TypeError, TimeoutError):
            self.send_error(400)
            return
        try:
            with path.open('xb') as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            # Persist the directory entry before deliberately losing the response.
            directory = os.open(self.server.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError:
            # Refuse a second PUT, even if its bytes happen to match.
            self.send_error(409, 'operation already exists; reconcile, do not replay')
            return
        self.log_message('COMMITTED object=%s bytes=%d ack_loss=%s',
                         path.name, len(content), self.headers.get('X-Ack-Loss') == '1')
        if self.headers.get('X-Ack-Loss') == '1':
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(201)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    with HTTPServer(('127.0.0.1', args.port), ObjectHandler) as server:
        server.root = args.root.resolve()
        server.serve_forever()


if __name__ == '__main__':
    main()
