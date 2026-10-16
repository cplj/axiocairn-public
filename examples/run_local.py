"""Run copied workers on real local dev servers, using a caller-supplied file.

No binary download, cloud, login, or shell commands. Processes started here are
stopped in finally; object bytes, receipts and runtime histories remain on disk.
"""
import argparse
import asyncio
import importlib.util
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

try:
    from axiocairn.file_tool import file_payload
    from axiocairn.local_http import LocalOpener
    from axiocairn.limits import MAX_JSON, MAX_OBJECT, read_bounded, read_file
    from axiocairn.verify import verify_receipts
    from axiocairn.sdk import canonical
except ModuleNotFoundError as error:
    if not error.name.startswith('axiocairn'):
        raise
    print(json.dumps({'state': 'ERROR', 'error': type(error).__name__,
          'message': 'Core SDK missing in this interpreter. Activate the venv and run python -m pip install . from the README directory; see README.md#install-and-check-prerequisites.'}))
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent
OPENER = LocalOpener()


def prerequisites(args):
    runtimes = ['temporal', 'inngest', 'restate'] if args.runtime == 'all' else [args.runtime]
    modules = {'temporal': ('temporalio',), 'inngest': ('inngest', 'fastapi', 'uvicorn'),
               'restate': ('restate', 'hypercorn')}
    binaries, missing = {}, []
    for runtime in runtimes:
        candidate = getattr(args, runtime + '_bin')
        resolved = shutil.which(candidate)
        if resolved is None:
            missing.append(f'{runtime}: binary {candidate!r} missing; install the vendor release or pass --{runtime}-bin /absolute/path/to/binary')
        else:
            binaries[runtime] = str(Path(resolved).resolve())
        absent = [module for module in modules[runtime] if importlib.util.find_spec(module) is None]
        if absent:
            missing.append(f'{runtime}: missing Python modules {", ".join(absent)}; activate the venv and run python -m pip install -r examples/requirements.txt')
    if missing:
        raise ValueError('; '.join(missing) + '; see README.md#install-and-check-prerequisites. No workers started; do not resend an existing action.')
    return binaries


def http(url, data=None, method=None, timeout=10):
    body = None if data is None else canonical(data).encode()
    request = urllib.request.Request(url, data=body, method=method,
                                    headers={'Content-Type': 'application/json'})
    with OPENER.open(request, timeout=timeout) as response:
        raw = read_bounded(response, MAX_JSON)
        return json.loads(raw) if raw else {}


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Processes:
    def __init__(self, root):
        self.root, self.items = root, []

    def start(self, name, command, extra=None):
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONDONTWRITEBYTECODE': '1',
               'DO_NOT_TRACK': '1', 'INNGEST_TELEMETRY_DISABLED': 'true', **(extra or {})}
        stream = (self.root / (name + '.log')).open('wb')
        process = subprocess.Popen(command, cwd=self.root, env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.items.append((process, stream))
        with (self.root / 'processes.jsonl').open('a') as log:
            log.write(json.dumps({'name': name, 'argv': command, 'pid': process.pid}) + '\n')
        return process

    async def ready(self, url, process):
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError(f'process exited {process.returncode}; inspect {self.root}')
            try:
                await asyncio.to_thread(http, url, timeout=1)
                return
            except Exception:
                await asyncio.sleep(.1)
        raise TimeoutError('local readiness timeout: ' + url)

    async def close(self):
        for process, stream in reversed(self.items):
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
            stream.close()


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')


async def invoke(runtime, request, processes, binary):
    work = processes.root
    if runtime == 'temporal':
        from temporalio.testing import WorkflowEnvironment
        # The binary is mandatory: WorkflowEnvironment cannot download anything.
        async with await WorkflowEnvironment.start_local(dev_server_existing_path=binary,
                dev_server_database_filename=str(work / 'temporal.db'), ui=False,
                dev_server_extra_args=['--ip', '127.0.0.1']) as environment:
            processes.start('temporal-worker', [sys.executable, str(ROOT / 'temporal_worker.py'),
                            '--address', environment.client.service_client.config.target_host])
            handle = await environment.client.start_workflow('FileRecovery', request,
                         id=request['operation'], task_queue='axiocairn-files',
                         execution_timeout=timedelta(seconds=50))
            result = await handle.result()
            history = await handle.fetch_history()
            (work / 'temporal-history.json').write_text(history.to_json(), encoding='utf-8')
            return result
    app_port, server_port = port(), port()
    server = f'http://127.0.0.1:{server_port}'
    if runtime == 'inngest':
        app = processes.start('inngest-worker', [sys.executable, str(ROOT / 'inngest_worker.py'),
                      '--port', str(app_port), '--server', server], {'INNGEST_DEV': server})
        endpoint = f'http://127.0.0.1:{app_port}/api/inngest'
        await processes.ready(endpoint, app)
        config = work / 'inngest.json'
        dump(config, {})
        process = processes.start('inngest-server', [binary, 'dev', '--config', str(config),
                   '--host', '127.0.0.1', '--port', str(server_port), '--no-discovery', '--no-poll',
                   '--connect-gateway-port', str(port()), '--connect-executor-grpc-port', str(port()),
                   '--connect-gateway-grpc-port', str(port()), '-u', endpoint],
                   {'INNGEST_CONFIG_DIR': str(work / 'inngest-config')})
        await processes.ready(server + '/health', process)
        dump(work / 'registration.json', await asyncio.to_thread(http, endpoint, method='PUT'))
        event = await asyncio.to_thread(http, server + '/e/local',
                       {'name': 'axiocairn/file', 'id': request['operation'], 'data': request}, 'POST')
        dump(work / 'event.json', event)
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            runs = await asyncio.to_thread(http, server + '/v1/events/' + event['ids'][0] + '/runs?observation=' + str(time.monotonic_ns()))
            data = runs.get('data', [])
            if data:
                detail = await asyncio.to_thread(http, server + '/v1/runs/' + data[0]['run_id'] + '?observation=' + str(time.monotonic_ns()))
                dump(work / 'inngest-history.json', detail)
                body = detail.get('data', detail)
                state = body.get('status', '').lower()
                if state == 'completed':
                    output = body['output']
                    return json.loads(output) if isinstance(output, str) else output
                if state in {'failed', 'cancelled'}:
                    raise RuntimeError('runtime failed: ' + json.dumps(body))
            await asyncio.sleep(.1)
        raise TimeoutError('Inngest result unavailable; UNKNOWN, do not resubmit event')
    admin_port, node_port = port(), port()
    admin = f'http://127.0.0.1:{admin_port}'
    config = work / 'restate.toml'
    config.write_text('node-name = "axiocairn-files"\ndisable-telemetry = true\n'
            f'bind-address = "127.0.0.1:{node_port}"\n'
            f'advertised-address = "http://127.0.0.1:{node_port}/"\n'
            f'base-dir = {json.dumps(str(work / "restate-data"))}\n'
            f'[ingress]\nbind-address = "127.0.0.1:{server_port}"\n'
            f'[admin]\nbind-address = "127.0.0.1:{admin_port}"\n')
    process = processes.start('restate-server', [binary, '--config-file', str(config)])
    await processes.ready(admin + '/health', process)
    app = processes.start('restate-worker', [sys.executable, str(ROOT / 'restate_worker.py'), '--port', str(app_port)])
    await processes.ready(f'http://127.0.0.1:{app_port}/discover', app)
    dump(work / 'deployment.json', await asyncio.to_thread(http, admin + '/deployments',
                                  {'uri': f'http://127.0.0.1:{app_port}'}, 'POST'))
    # Invocation ID in the response headers is saved for independent runtime readback.
    raw = urllib.request.Request(server + '/AxiocairnFiles/recover',
            data=json.dumps(request).encode(), headers={'Content-Type': 'application/json',
                                                       'idempotency-key': request['operation']})
    def call():
        with OPENER.open(raw, timeout=50) as response:
            dump(work / 'restate-invocation.json', dict(response.headers))
            return json.loads(read_bounded(response, MAX_JSON))
    return await asyncio.to_thread(call)


async def main(args):
    started = time.monotonic()
    binaries = prerequisites(args)
    if args.check:
        versions = {}
        for runtime, binary in binaries.items():
            result = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=10, check=True)
            versions[runtime] = {'path': binary, 'version': result.stdout.strip()}
        print(json.dumps({'prerequisites': 'ready', 'binaries': versions, 'effects_started': False}), flush=True)
        return 0
    source = args.source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('source must be an existing file of at most 16 MiB')
    runtimes = ['temporal', 'inngest', 'restate'] if args.runtime == 'all' else [args.runtime]
    args.work.mkdir(parents=True, exist_ok=False)
    work = args.work.resolve()
    results = []
    for runtime in runtimes:
        directory = work / runtime
        directory.mkdir()
        processes = Processes(directory)
        try:
            object_port = port()
            url = f'http://127.0.0.1:{object_port}'
            process = processes.start('object-server', [sys.executable, '-m', 'axiocairn.file_service',
                       '--root', str(directory / 'objects'), '--port', str(object_port)])
            await processes.ready(url + '/health', process)
            action = 'file-' + uuid.uuid4().hex
            request = {'operation': action, 'version': 'v1', 'object_url': url,
                       'source': str(source), 'payload': file_payload(action, read_file(source, MAX_OBJECT)),
                       'intent_db': str(directory / 'intents.db'), 'receipts': str(directory / 'receipts'),
                       'ack_loss': args.ack_loss, 'query_unavailable': args.query_unavailable}
            dump(directory / 'request.json', request)
            result = await asyncio.wait_for(invoke(runtime, request, processes, binaries[runtime]), timeout=90)
            dump(directory / 'result.json', result)
            verified = verify_receipts(directory / 'receipts') if result['state'] != 'ERROR' else {'verified': False}
            # Read the actual external object separately from the receipt snapshot.
            actual = read_file(directory / 'objects' / action, MAX_OBJECT)
            if actual != read_file(source, MAX_OBJECT):
                raise ValueError('external object differs from caller source')
            row = {'runtime': runtime, 'result': result, 'verification': verified,
                   'external_object': str(directory / 'objects' / action)}
            results.append(row)
            print(json.dumps(row), flush=True)
        finally:
            await processes.close()
    summary = {'elapsed_seconds': round(time.monotonic() - started, 3), 'results': results}
    dump(work / 'summary.json', summary)
    return 0 if all(r['result']['state'] == 'COMPLETED' and r['verification'].get('verified') is True
                    for r in results) else 3


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=['all', 'temporal', 'inngest', 'restate'], default='all')
    parser.add_argument('--check', action='store_true', help='check SDK modules, binary paths and versions; start no workers and write no objects')
    parser.add_argument('--source', type=Path, help='existing caller-selected non-secret file, at most 16 MiB; required unless --check')
    parser.add_argument('--work', type=Path, help='new directory; required unless --check; existing data is never overwritten')
    parser.add_argument('--ack-loss', action='store_true', help='server commits bytes, then closes before replying')
    parser.add_argument('--query-unavailable', action='store_true', help='GET returns 503; expect UNKNOWN and exit 3')
    parser.add_argument('--temporal-bin', default='temporal')
    parser.add_argument('--inngest-bin', default='inngest')
    parser.add_argument('--restate-bin', default='restate-server')
    try:
        args = parser.parse_args()
        if not args.check and (args.source is None or args.work is None):
            parser.error('--source FILE and --work NEW_DIR are required unless --check; see README.md#run-a-real-file-through-three-real-runtimes')
        raise SystemExit(asyncio.run(main(args)))
    except Exception as error:
        print(json.dumps({'state': 'ERROR', 'error': type(error).__name__, 'message': str(error) + '; see README.md#common-errors; preserve existing evidence and do not resend the action.'}))
        raise SystemExit(2)
