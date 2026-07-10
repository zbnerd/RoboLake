# RoboLake

RoboLake is an open-source robot dataset transfer and registry platform. M0 provides a runnable
FastAPI/Typer development skeleton with PostgreSQL and MinIO connectivity. Dataset scan, push, pull,
and registration behavior starts in later milestones and is intentionally absent here.

## Requirements

- Python 3.12
- [uv 0.11.28+](https://docs.astral.sh/uv/getting-started/installation/)
- Docker Engine with Docker Compose

Install the pinned uv version without changing shell configuration:

```bash
curl -LsSf https://astral.sh/uv/0.11.28/install.sh \
  | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
```

## Quickstart

From the repository root, run the required M0 workflow:

```bash
docker compose up -d
uv sync
uv run alembic upgrade head
uv run robolake --help
uv run pytest
uv run ruff check .
uv run mypy robolake
```

Local services use ports chosen to avoid common workstation conflicts:

- API and OpenAPI: <http://localhost:18000>, <http://localhost:18000/docs>
- PostgreSQL: `localhost:15432`
- MinIO S3 API and console: <http://localhost:19000>, <http://localhost:19001>

Check container state with `docker compose ps`; stop services with `docker compose down`.

## Configuration

[`.env.example`](.env.example) contains synthetic local-development values only. RoboLake loads it
first and lets an ignored `.env` override values. All application settings use the `ROBOLAKE_`
prefix. Compose also reads PostgreSQL and MinIO bootstrap values from these files. Never place real
credentials in Git.

Host ports can be changed without editing Compose:

```bash
ROBOLAKE_API_PORT=28000 ROBOLAKE_MINIO_PORT=29000 docker compose up -d
```

## Health

`GET /health` checks the application, PostgreSQL, and the configured object-storage bucket. Healthy
dependencies return HTTP 200:

```bash
curl --fail http://localhost:18000/health
```

```json
{"status":"ok","components":{"application":"ok","database":"ok","object_storage":"ok"}}
```

A failed dependency returns HTTP 503 and `down` without exposing provider exception details.

## Synthetic example data

Create a small deterministic nested tree containing no real robot data:

```bash
uv run robolake example generate examples/synthetic-dataset --seed 7
```

The command refuses to overwrite a non-empty destination. The default generated directory is
ignored by Git; remove it before changing the seed or rerunning the command.

## Development tasks

```bash
make up            # start and wait for local services
make migrate       # apply Alembic migrations
make unit          # isolated tests
make integration   # real PostgreSQL and MinIO tests
make check         # Ruff, formatting, mypy, and all tests
make down          # stop services without deleting volumes
```

## Repository structure

```text
apps/api/                  FastAPI transport and composition
apps/cli/                  Typer transport and composition
robolake/domain/           framework-free domain boundary
robolake/application/      framework-free use cases and ports
robolake/infrastructure/   SQLAlchemy, S3, settings, local adapters
migrations/                Alembic environment and revisions
tests/unit/                isolated behavior and boundary tests
tests/integration/         real PostgreSQL and MinIO contract tests
docs/adr/                  architecture decisions
```

See [the v0.1 architecture](docs/ARCHITECTURE_V0_1.md) and
[M0–M5 roadmap](docs/ROADMAP.md) for accepted scope. M0 adds no authentication, upload/download
workflow, Kafka, Airflow, Iceberg, ROS2 integration, frontend, or model-training capability.
