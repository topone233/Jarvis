# Jarvis Core

Jarvis Core is the local FastAPI service used by the Jarvis frontend. It owns
model configuration, conversations, context lifecycle, persistent memory,
knowledge ingestion, retrieval, audit events, and the recycle bin.

## Development

From this directory, run:

    uv sync --group dev
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8787

On the first launch, call the setup API to select a user-owned data directory.
For development, set JARVIS_BOOTSTRAP_DIR to an isolated temporary directory.

## Quality gate

Every change must pass all four checks before it is considered done:

    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    uv run pytest

The same tool versions are pinned in the `dev` dependency group, so `uv sync
--group dev` is enough to reproduce the gate locally. `[tool.mypy]` and
`[tool.ruff]` in pyproject.toml are the single source of truth for the rules;
do not pass flags on the command line, or CI and local runs will drift apart.

`B008` is disabled because FastAPI expresses dependency injection as
`Depends(...)` defaults in the signature, which that rule flags as a
function-call-in-default-argument bug.

