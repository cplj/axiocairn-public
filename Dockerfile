FROM python:3.14.8-bookworm@sha256:7ee7e4d4fb42c3ad45b8fdc473b64ec69c3cf6e80ae5d52f6fa55f77fac29027

ENV PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/axiocairn
COPY LICENSE NOTICE pyproject.toml _build.py README.md __init__.py sdk.py recover_existing.py interface.py cli.py api.py file_tool.py file_service.py verify.py limits.py local_http.py ./
RUN command -v curl && python -m pip install .

WORKDIR /data
USER 65532:65532
CMD ["python", "-m", "axiocairn.api", "--port", "8765"]
