# Axiocairn 0.2.0

When a tool writes successfully but loses its reply, reconcile the original operation against effect evidence before deciding what to do next.

Install the core from this directory with Python 3.11+: `python3 -m venv .venv && .venv/bin/python -m pip install .`

A real local file run produced this offline verification output (operation ID varies):

```json
{"detail":"offline bundle consistency verified; origin and current server state are not authenticated","id":"file-5cbcfa7db8d548c2a28841594a0eec77","sha256":"e4ab19dc7199f9f15fac3d9cb62743637950cfab70dd9238715ad9d73a654042","size":678260,"state":"COMPLETED","verified":true}
```

[Install and check prerequisites](#install-and-check-prerequisites) → [run your file](#run-a-real-file-through-three-real-runtimes) → [verify its receipts](#offline-receipt-verification). If anything fails, use [common errors](#common-errors). Agents: start at [For AI agents](#for-ai-agents) or [llms.txt](llms.txt).

The core SDK and offline verifier use only the standard library. The three optional workers require separate local server binaries and Python SDKs. Recovery and offline verification never execute or retry the original action. No account, credentials, cloud deployment or paid service is required.

## Install and check prerequisites

Use macOS or Linux with Python 3.11+, pip and venv. On Linux, if `python3 -m venv` reports that ensurepip is unavailable, install your distribution's matching Python venv package (for example `python3-venv` on Debian/Ubuntu), then create the venv again. If Python is older than 3.11, select a 3.11+ interpreter before installation. PyPI publication is pending: install this checkout with `python -m pip install .`, not `pip install axiocairn`.

For the real worker run, install **Temporal CLI 1.9.1**, **Inngest 1.46.0**, and **restate-server 1.8.0** from their pinned vendor release pages. Check `uname -s` and `uname -m`, then choose the matching archives below; use the vendor checksums alongside each release. Restate needs the **server**, not `restate-cli`.

| Machine | [Temporal 1.9.1](https://github.com/temporalio/cli/releases/tag/v1.9.1) | [Inngest 1.46.0](https://github.com/inngest/inngest/releases/tag/v1.46.0) | [Restate 1.8.0](https://github.com/restatedev/restate/releases/tag/v1.8.0) |
| --- | --- | --- | --- |
| macOS arm64 | `temporal_cli_1.9.1_darwin_arm64.tar.gz` | `inngest_1.46.0_darwin_arm64.tar.gz` | `restate-server-aarch64-apple-darwin.tar.xz` |
| macOS x86_64 | `temporal_cli_1.9.1_darwin_amd64.tar.gz` | `inngest_1.46.0_darwin_amd64.tar.gz` | `restate-server-x86_64-apple-darwin.tar.xz` |
| Linux aarch64 | `temporal_cli_1.9.1_linux_arm64.tar.gz` | `inngest_1.46.0_linux_arm64.tar.gz` | `restate-server-aarch64-unknown-linux-musl.tar.xz` |
| Linux x86_64 | `temporal_cli_1.9.1_linux_amd64.tar.gz` | `inngest_1.46.0_linux_amd64.tar.gz` | `restate-server-x86_64-unknown-linux-musl.tar.xz` |

Extract each archive with `tar -xf /path/to/archive -C /path/to/its/empty-directory`, locate `temporal`, `inngest`, and `restate-server` in the extracted directories, and add their directories to PATH: `export PATH="/path/to/temporal-dir:/path/to/inngest-dir:/path/to/restate-dir:$PATH"`. Preserve execute permissions; on macOS, follow your OS's approval prompt for vendor binaries. Restate server uses its vendor BSL license; this package does not relicense vendor software. The runner never installs or downloads a server.

Check all three binaries **before installing SDKs**. Copy this complete block; a failure stops the block at its original error:

```sh
(
set -eu
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Use Python 3.11+; see README.md#install-and-check-prerequisites")'
for binary in temporal inngest restate-server; do
  command -v "$binary" || { echo "Missing $binary: install the matching vendor archive and add its directory to PATH; see README.md#install-and-check-prerequisites" >&2; exit 2; }
  "$binary" --version
done
)
```

Compare the printed versions to the pinned versions above. If binaries live outside PATH, pass their absolute paths with `--temporal-bin`, `--inngest-bin` and `--restate-bin` to **both** `--check` and the runner instead. After installing the core and `examples/requirements.txt` in an activated venv, `python -B examples/run_local.py --check` checks binary paths, versions and all selected worker SDK imports without starting servers or creating effects. `--runtime temporal --check` checks just Temporal.

## Run a real file through three real runtimes

The external effect is an HTTP PUT of a file **you select** into a separate local object server's directory. The worker uses `HTTPFileTool` with `RecoverySDK`; a subsequent HTTP GET hashes the actual stored bytes. It does not read the simulated receipt table. `--ack-loss` makes the object server write and fsync the file, then close the socket before sending its acknowledgement. Reconciliation queries the same operation; it does not issue another PUT.

From the directory containing this README, copy the complete block below. `/etc/services` is an existing, non-secret operating-system file on macOS/Linux, not a bundled fixture. Replace it with your own non-secret file (at most 16 MiB) to run your own bytes. The fresh venv does not use system site-packages. The first runtime dependency installation requires access to the package index or a populated pip cache; execution and verification need only local servers. The target is under ten minutes after the binaries are available; cold download time depends on your connection. Complete the [binary precheck](#install-and-check-prerequisites) first. Copy the whole subshell block so an installation, precheck or runner failure stops immediately.

```sh
(
set -eu
RUN_ROOT="$(mktemp -d)"
python3 -m venv "$RUN_ROOT/venv"
. "$RUN_ROOT/venv/bin/activate"
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 python -m pip install .
python -m pip install --disable-pip-version-check -r examples/requirements.txt
python -B examples/run_local.py --check
SOURCE_FILE=/etc/services
python -B examples/run_local.py --runtime all --source "$SOURCE_FILE" --work "$RUN_ROOT/run" --ack-loss
for runtime in temporal inngest restate; do
  axiocairn verify --receipts "$RUN_ROOT/run/$runtime/receipts" || exit $?
  printf 'Receipt bundle: %s/run/%s/receipts\n' "$RUN_ROOT" "$runtime"
done
)
```

The block runs in a subshell. For later commands, set `RUN_ROOT` to the temporary directory shown in the printed receipt paths and set `SOURCE_FILE` to the same source file.

Expected: three runtime results with `state: "COMPLETED"`, each paired with `verified: true`, followed by three successful offline verifications. The runner exits 0 only after all selected runtimes return COMPLETED, their evidence verifies, and their actual external object bytes equal your selected source file. It saves request, reconcile report, intent database, object file, receipt bundle, server/worker logs and runtime execution records under `$RUN_ROOT/run/<runtime>/`. All servers and workers it starts are stopped before exit. The original input file is read only. A new `--work` directory is required for every run; existing evidence is not overwritten.

For execution with outbound networking blocked on macOS, replace the runner line with:

```sh
sandbox-exec -f examples/local-only.sb python -B examples/run_local.py --runtime all --source "$SOURCE_FILE" --work "$RUN_ROOT/offline-run" --ack-loss
```

This still uses real dev servers and real worker processes. The sandbox profile permits loopback networking only. No browser UI is needed.

## Copy a worker into your application

| Runtime | Copy this worker | Local execution primitive |
| --- | --- | --- |
| Temporal | [examples/temporal_worker.py](examples/temporal_worker.py) | Workflow schedules an activity with `maximum_attempts=1` |
| Inngest | [examples/inngest_worker.py](examples/inngest_worker.py) | Event invokes a memoized `ctx.step.run`, function retries disabled |
| Restate | [examples/restate_worker.py](examples/restate_worker.py) | Service invokes `ctx.run_typed` to journal the result |

[examples/run_local.py](examples/run_local.py) starts, registers and invokes these exact workers against actual local servers. Select `--runtime temporal`, `--runtime inngest` or `--runtime restate` to run just one. It is an executable reference for the client submission and runtime result lookup, including Inngest's nested `data`/`output` response. A completed runtime invocation may carry a domain UNKNOWN or ERROR: inspect the domain `state` and the process exit code.

Each worker calls the same installed SDK entry point:

```python
from axiocairn import run_file_operation
report = run_file_operation(request)
assert report["state"] == "COMPLETED", report
```

`request` is the saved `request.json`: `operation` (stable identifier), `version`, `object_url` (loopback HTTP server), `source` (existing file), `payload` (`object`, SHA-256, byte size), `intent_db`, and a new `receipts` directory. `ack_loss` and `query_unavailable` are explicit development fault switches. Intent identity and payload are immutable. The runner supplies these fields from your selected file; it does not substitute built-in sample bytes. The file-object server is a bounded local development integration, not an authenticated production storage service. For another provider implement `identity`, `execute` and `query` using that provider's actual effect/evidence contract; do not infer retry safety from a missing response.

To test unresolved evidence, use a new directory:

```sh
python -B examples/run_local.py --runtime all --source "$SOURCE_FILE" --work "$RUN_ROOT/unknown" --ack-loss --query-unavailable
# Expected process exit: 3. Each object exists, but each report is UNKNOWN.
axiocairn verify --receipts "$RUN_ROOT/unknown/temporal/receipts"
# Expected exit: 3, verified: false. UNKNOWN never becomes a success.
```

Once evidence access returns, use the original saved request with `reconcile_only=True`, `query_unavailable=False` and a new `receipts` export directory while the **same** object server URL and intent database remain available. Call `run_file_operation(request)` in a fresh runtime invocation. Reconciliation only queries; it does not repeat the PUT. The convenience runner stops its ephemeral servers; it does not provide automatic restart or recovery of their URLs. For long-lived integration, run the object server and worker yourself using the worker files and server configuration saved by the runner.

## Offline receipt verification

```sh
axiocairn verify --receipts /path/to/one/runtime/receipts
```

The directory contains `intent.json`, `report.json` and, when readable, `object.bin` (the independent GET snapshot). The verifier makes **no network calls** and opens no intent database. It checks strict JSON, report/intent operation, version, adapter identity and payload agreement, then the snapshot's SHA-256 and size. Exit 0 requires a COMPLETED report and a matching snapshot. Unresolved states exit 3 with `verified: false`; invalid JSON, identity mismatch, missing evidence and tampered content exit 2 with ERROR. Empty evidence is never success.

This proves consistency of exported evidence under the local trust model. It does not authenticate who produced the bundle or prove the object still exists now. An adversary who rewrites the entire bundle can forge it. No signatures, remote account verification, concurrent-writer safety or distributed exactly-once guarantee is claimed. Snapshot files contain the selected input's bytes; choose non-secret inputs and handle your own evidence accordingly.

## For AI agents

Reconcile an uncertain tool action using the original operation and observable effect evidence. Read the [full prerequisites and binary precheck](#install-and-check-prerequisites) before the [real worker block](#run-a-real-file-through-three-real-runtimes); installing the core alone does not install worker SDKs or server binaries.

Core installation from this checkout: `python3 -m venv .venv && .venv/bin/python -m pip install .`. Activate with `. .venv/bin/activate`. For real workers also run `python -m pip install -r examples/requirements.txt`, then `python -B examples/run_local.py --check` with the same binary flags as execution. Use an existing non-secret source file and a fresh work directory; the worker run writes an actual local object. Request permission for that effect under your caller's policy.

The minimum read-only call is `axiocairn verify --receipts "/absolute/path/to/run/temporal/receipts"`. Use the actual directory printed at the end of the real worker block, or the `receipts` field in its saved `run/<runtime>/request.json`; substitute `inngest` or `restate` as needed. Do not invent a receipt directory or rerun the worker just because verification fails. Required bundle files are `intent.json`, `report.json`, and `object.bin` for COMPLETED evidence.

Input/output contracts: [file-object bundle](examples/receipt-bundle.schema.json), [SQLite recovery request](docs/schemas/request.json), [recovery result](docs/schemas/result.json), [error envelope](docs/schemas/error.json), and [HTTP API](openapi.yaml). Recovery CLI stdout is one JSON object, with `state`, `error` and `message` on invalid input; offline verification returns `state` and `verified` for readable domain reports. Invalid arguments use stderr. Exit 0 means COMPLETED (offline verify also requires `verified: true`), exit 3 means unresolved, and exit 2 means an error. The runner emits one JSON row per runtime and writes `summary.json` on a completed run; `--check` returns prerequisite information, not an operation result.

On ERROR, read `message`, correct the input or restore original evidence using [common errors](#common-errors). On UNKNOWN, preserve the original operation ID, version, stores and URL; wait for evidence access before querying again. Never automatically resubmit an action. Local integration results do not establish customer acceptance or production reliability.

## Common errors

| Symptom | Next step |
| --- | --- |
| `python3` is too old, or venv/ensurepip is missing | Use Python 3.11+ and its matching venv package; see [prerequisites](#install-and-check-prerequisites). |
| `axiocairn: command not found` or `No module named axiocairn` | Activate the venv used for installation; from this README directory run `python -m pip install .`. Confirm `python -m axiocairn.cli --version`. |
| Missing `temporal`, `inngest`, or `restate-server` | Install the matching archive above, add its binary directory to PATH, or pass the corresponding `--*-bin /absolute/path`. Repeat `--check` before starting workers. |
| Missing worker Python module | In the same activated venv run `python -m pip install -r examples/requirements.txt`, then repeat `--check`. |
| Empty stdin, bad JSON, missing fields, or a missing request file | Supply the original seven-field JSON with `axiocairn --request "/actual/path/request.json"`; see [request schema](docs/schemas/request.json). Use the existing original stores. |
| Missing `intent.json`, `report.json`, or required `object.bin` | Verify the directory printed by the runner or saved in `request.json`, using `axiocairn verify --receipts "/actual/path/receipts"`. Restore original exports; do not fabricate evidence or repeat the PUT. |
| Existing work directory | Preserve its evidence. Use a fresh directory only for a deliberately new test; reconcile an existing operation using its original request. |
| UNKNOWN / exit 3, or identity/version/snapshot mismatch | Keep the original ID, version, stores and object URL. Restore access or review the conflicting evidence. A runtime or HTTP success alone is insufficient; do not resend the action. |

## Legacy synthetic 30-second quick start

Run this block in the directory containing this README on macOS or Linux. It creates a fresh venv and synthetic example stores in temporary directories. No existing customer data is required. `pip install .` is the complete installation command; `PIP_NO_INDEX` below also proves offline installation.

```sh
VENV_DIR="$(mktemp -d)"
python3 -m venv "$VENV_DIR"
. "$VENV_DIR/bin/activate"
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 pip install .
WORK_DIR="$(mktemp -d)"
export WORK_DIR
python - <<'PY'
import json, os
from pathlib import Path
from axiocairn import RecoverySDK, SimulatedTool
root = Path(os.environ['WORK_DIR'])
sdk = RecoverySDK(root / 'intent.db')
tool = SimulatedTool(root / 'receipts.db', 'payments', 'ledger', 'sandbox-a')
try:
    tool.query_available = False
    result = sdk.run('action-1', tool, {'amount': 1}, 'v1', 'timeout_after')
    assert result['state'] == 'UNKNOWN'
finally:
    sdk.db.close()
    tool.db.close()
request = dict(intent_db=str(root / 'intent.db'), receipt_db=str(root / 'receipts.db'),
               name='payments', kind='ledger', account='sandbox-a', action='action-1', version='v1')
(root / 'request.json').write_text(json.dumps(request), encoding='utf-8')
print('Prepared one receipt after a lost acknowledgement.')
PY
axiocairn --version
axiocairn --help
axiocairn --request "$WORK_DIR/request.json"
```

Expected version: `0.2.0`. The final command prints one JSON object with `state` equal to `COMPLETED` and exits 0. It observes the receipt already created by the synthetic tool; it does not create another effect.

## Input and output

CLI input is a JSON file (`--request FILE`) or standard input (the default):

```sh
cat "$WORK_DIR/request.json" | axiocairn
```

All seven fields are required nonempty strings; extra fields, duplicate JSON keys, invalid JSON, and nonfinite numbers are rejected. Use absolute paths for the API; relative paths resolve against the server's working directory. Use the original name, kind, account, action ID and version.

```json
{
  "intent_db": "/path/to/original/intent.db",
  "receipt_db": "/path/to/original/receipts.db",
  "name": "payments",
  "kind": "ledger",
  "account": "sandbox-a",
  "action": "action-1",
  "version": "v1"
}
```

A matching synthetic receipt returns:

```json
{"detail":"one matching effect observed","id":"action-1","payload":"{\"amount\":1}","state":"COMPLETED","tool":"[\"payments\",\"ledger\",\"sandbox-a\"]","version":"v1"}
```

For an intent not already `COMPLETED`, an unavailable receipt query returns the same fields with `state: "UNKNOWN"` and `detail: "receipt query unavailable; no retry"`. For example, an exclusive lock on the receipt database prevents the query and yields `UNKNOWN` after SQLite's default five-second wait. This is distinct from a missing store or incompatible schema, which is an error. Missing files are never created by recovery.

| State | Meaning and next step | CLI exit |
| --- | --- | --- |
| `COMPLETED` | One matching receipt observed. Check the business outcome separately. | 0 |
| `UNKNOWN` | Receipt query unavailable. Preserve the original ID and stores; query again only when evidence access is restored. Do not blindly retry the action. | 3 |
| `MANUAL_REVIEW` | Conflicting, duplicate, malformed or nonfinite receipts, an uncertain non-idempotent action, or missing/unavailable evidence for a previously completed intent. See persistence rules below. | 3 |
| `VERSION_MISMATCH` | Caller version differs. Stored state is preserved; review the original version. | 3 |
| `RETRY_SAFE` | Empty query under the toy ledger's deduplication contract. Recovery still does not execute anything. | 3 |
| `PREPARED` | Intent recorded, no receipt and no execution recorded. Recovery leaves it prepared. | 3 |
| `ERROR` | Invalid request, missing operation/store, incompatible schema or local execution failure. Correct the input or find the original stores. | 2 |

If an intent is already stored as `COMPLETED`, an empty or unavailable receipt query returns `MANUAL_REVIEW` without changing any stored intent field, including the original state and detail. It never downgrades that intent to `RETRY_SAFE` or `UNKNOWN`. Restore access to the original receipt store and reconcile again; do not repeat the action. A matching receipt still confirms `COMPLETED`. New conflicting, duplicate or malformed evidence can overwrite the stored decision with a durable `MANUAL_REVIEW` stop; that stop has no reset mechanism. Malformed receipts include NULL or other non-string payloads and JSON nested beyond the interpreter's recursion limit.

Errors are JSON on stdout, for example `{"state":"ERROR","error":"ValueError","message":"account must be a nonempty string"}`. Argument usage errors such as an unknown flag use argparse's stderr and exit 2. Inspect the state even when HTTP returns 200; only `COMPLETED` means a matching receipt was observed.

## Local HTTP API

After the quick start, start the API in the activated shell:

```sh
python -m axiocairn.api --port 8765
```

It prints `Listening on http://127.0.0.1:8765/recover`. In a second terminal, use the **actual** request file path created above:

```sh
curl -sS -X POST http://127.0.0.1:8765/recover \
  -H 'Content-Type: application/json' \
  --data-binary @/absolute/path/to/request.json
```

Alternatively, stop the foreground server with Ctrl-C, then copy this complete block in the original activated shell. It starts the API, waits for its loopback socket, sends the same JSON used by the CLI, checks the result, and stops the server:

```sh
python -m axiocairn.api --port 8765 > "$WORK_DIR/api.log" 2>&1 &
API_PID=$!
python - <<'PY'
import json, os, socket, time, urllib.request
from axiocairn.local_http import LocalOpener
from axiocairn.limits import MAX_JSON, read_bounded
from pathlib import Path
for attempt in range(50):
    try:
        with socket.create_connection(('127.0.0.1', 8765), timeout=0.1):
            break
    except OSError:
        time.sleep(0.1)
else:
    raise RuntimeError('API did not start; inspect api.log')
body = (Path(os.environ['WORK_DIR']) / 'request.json').read_bytes()
request = urllib.request.Request('http://127.0.0.1:8765/recover', data=body,
                                 headers={'Content-Type': 'application/json'})
with LocalOpener().open(request, timeout=15) as response:
    result = json.loads(read_bounded(response, MAX_JSON))
assert result['state'] == 'COMPLETED', result
print(json.dumps(result, sort_keys=True))
PY
kill "$API_PID"
wait "$API_PID" 2>/dev/null || true
```

`POST /recover` has exactly the same input and result as the CLI. HTTP 200 contains a valid recovery result, including unresolved states. HTTP 400 reports invalid JSON, fields or identity; 422 reports local SQLite/store errors; 404 an unknown endpoint; 405 an unsupported method; 415 an incorrect content type; 413 a body over 65,536 bytes; 408 an incomplete/slow body; and 500 an unexpected internal failure. All HTTP error responses are JSON with `state: "ERROR"`. Requests require `Content-Length`; chunked transfer is unsupported.

The server is bound to `127.0.0.1`, processes one request at a time, and has no authentication or TLS. It trusts local callers, who can select local database paths. Run it only on your own machine with trusted processes; stop it when finished. It is not a production or multi-user server.

## Local Docker image

The single image includes the CLI and SDK and starts the API by default. Docker must already be installed and running, with `python:3.14-bookworm` cached locally. The first command checks for that base image before building; it does not pull a missing image. The full Python base is used because it includes curl; the build checks its presence without installing more packages. Docker execution is not yet verified on the author's machine because its daemon is unavailable.

After the quick start above, run these two lines from this README's directory. The build uses no package index and disables networking for build steps. The container has no external network, published ports, credentials, or Docker socket mount. Only the synthetic stores in `$WORK_DIR` are mounted; do not mount a home directory or secrets. The numeric user matches the owner of those temporary stores.

```sh
docker image inspect python:3.14-bookworm >/dev/null && docker build --pull=false --network=none -t axiocairn:0.2.0 .
docker run -d --pull never --name axiocairn-v02 --network none --user "$(id -u):$(id -g)" --mount "type=bind,src=$WORK_DIR,dst=/data" axiocairn:0.2.0
```

The API listens on loopback **inside the container**. Run curl there with `docker exec`; host `localhost` is a different network namespace. The paths in this request are container paths to the mounted original stores:

```sh
docker exec axiocairn-v02 curl -sS --fail-with-body -X POST http://127.0.0.1:8765/recover -H 'Content-Type: application/json' --data '{"intent_db":"/data/intent.db","receipt_db":"/data/receipts.db","name":"payments","kind":"ledger","account":"sandbox-a","action":"action-1","version":"v1"}'
```

After the API startup message appears in `docker logs axiocairn-v02`, this request returns the quick start's `COMPLETED` JSON. As with the native API, HTTP 200 can also contain `UNKNOWN` or another unresolved state; inspect `state`. The CLI in the same image accepts that JSON on stdin:

```sh
docker exec -i axiocairn-v02 axiocairn <<'JSON'
{"intent_db":"/data/intent.db","receipt_db":"/data/receipts.db","name":"payments","kind":"ledger","account":"sandbox-a","action":"action-1","version":"v1"}
JSON
```

Use `docker stop axiocairn-v02` when finished. The container and synthetic stores remain on disk; use a different name for another run. Docker is an optional local delivery method, not cloud hosting or production validation.

## Python SDK

```python
from axiocairn import recover

result = recover(
    intent_db="/path/to/original/intent.db",
    receipt_db="/path/to/original/receipts.db",
    name="payments", kind="ledger", account="sandbox-a",
    action="action-1", version="v1",
)
print(result["state"])
```

The SDK returns the same result dictionary. It raises `ValueError`, `KeyError` or `sqlite3.Error` for invalid identities, missing operations or local store errors, rather than converting them into CLI/API error envelopes. `RecoverySDK` and `SimulatedTool` are also exported for explicit fixture creation and Python adapter integration. Their constructors create new stores; `recover()` opens existing stores only. Close each object's `.db` connection when finished.

## Tests and CI example

After installation, run from this directory:

```sh
python -B -m unittest discover -s . -p 'test_*.py' -v
python -B demo.py
```

Tests cover normal results, errors, unknown evidence, account/version boundaries, duplicate/malformed receipts, CLI/API agreement and no execution during recovery. The demo's ordinary-script comparison is a **tie**, with no demonstrated speed or commercial advantage. The file `ci/github-actions.yml` is an example workflow; copy it to your repository's `.github/workflows/` only when you intend to enable CI. Nothing is pushed or enabled by this package.

## What it does not do

The legacy SQLite adapter supports two **simulated** tools: a deduplicating `ledger` and a non-idempotent `append`. The HTTP file-object adapter above adds a real local effect integration. It does not ship a live SaaS adapter, authenticate account labels or receipts, verify business outcomes, migrate old schemas, reset manual-review stops, guarantee distributed exactly-once execution or concurrency safety, perform arbitrary rollback, or execute retries through CLI/API. Caller-supplied labels must refer to the original account and store; identical labels on different stores can defeat the identity check. A well-formed dishonest receipt is not detected. Synthetic test passes are not production reliability, customer acceptance, payment or measured savings.

## Input and HTTP safety bounds

JSON requests and each receipt or bundle metadata file are limited to 65,536 UTF-8 bytes. Receipt queries accept at most 1,000 rows and 1 MiB of encoded receipt data per operation. File sources, HTTP object responses and offline object snapshots are limited to 16 MiB. Oversized inputs produce an explicit input-limit error; oversized receipt evidence produces MANUAL_REVIEW with the limit in its detail and no retry clearance.

Direct HTTP clients accept only the exact literal `http://127.0.0.1:PORT` development-service host, disable proxies, and never follow redirects. This loopback-only exception is required by the local examples; other loopback aliases, private networks, link-local and metadata endpoints, and DNS names are rejected. The local API assumes a trusted caller; it does not provide authentication or tenant isolation.
