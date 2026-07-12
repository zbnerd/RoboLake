# RoboLake

RoboLake is an open-source robot dataset transfer and registry platform. M1 scans a local
regular-file tree, registers an immutable DatasetVersion, transfers unique SHA-256-addressed Blobs
directly to S3-compatible storage, and reconstructs the tree byte-for-byte.

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

From the repository root, start the M1 development environment:

```bash
docker compose up -d --wait
uv sync
uv run alembic upgrade head
uv run robolake example generate /tmp/robolake-demo --seed 7
uv run robolake push /tmp/robolake-demo --dataset demo/pick-place
uv run robolake status demo/pick-place@v1
uv run robolake manifest demo/pick-place@v1
uv run robolake pull demo/pick-place@v1 --output /tmp/robolake-restored
diff -qr /tmp/robolake-demo /tmp/robolake-restored
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

Run the repeatable synthetic end-to-end demonstration with:

```bash
scripts/demo-v01.sh
```

`push` is idempotent for an unchanged manifest and resumes at verified whole-Blob boundaries. M1
processes unique Blobs sequentially and rejects a file above 5,000,000,000 bytes before creating
registry state. Multipart and within-file resume begin in M2.

## Development tasks

```bash
make up            # start and wait for local services
make migrate       # apply Alembic migrations
make unit          # isolated tests
make integration   # real PostgreSQL and MinIO tests
make check         # Ruff, formatting, mypy, and all tests
make demo          # synthetic push/status/manifest/pull verification
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
[M0–M5 roadmap](docs/ROADMAP.md) for accepted scope. M1 adds no authentication, Kafka, Airflow,
Iceberg, ROS2 integration, frontend, or model-training capability.
