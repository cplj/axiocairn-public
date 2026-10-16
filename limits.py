"""Fixed input bounds shared by local entry points and adapters."""
from pathlib import Path

MAX_JSON = 65536
MAX_RECEIPTS = 1000
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_OBJECT = 16 * 1024 * 1024


class InputLimitError(ValueError):
    """The input exceeds a documented size or count bound."""


def check_size(value, limit=MAX_JSON):
    if len(value) > limit or (isinstance(value, str) and len(value.encode('utf-8')) > limit):
        raise InputLimitError(f'input exceeds {limit} bytes')
    return value


def read_bounded(stream, limit):
    return check_size(stream.read(limit + 1), limit)


def read_file(path, limit):
    with Path(path).open('rb') as source:
        return read_bounded(source, limit)
