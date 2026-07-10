# RoboLake

Data Lake for Physical AI, powered by FastAPI.

## Requirements

- Python 3.12+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On Debian/Ubuntu, install `python3.12-venv` first if `venv` reports that
`ensurepip` is unavailable.

## Run

```bash
fastapi dev
```

- API: http://127.0.0.1:8000
- OpenAPI docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Verify

```bash
pytest
ruff check .
ruff format --check .
```
