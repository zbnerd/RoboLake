# RoboLake M0 Development Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a runnable, typed, tested FastAPI/Typer modular-monolith skeleton connected to
PostgreSQL and MinIO, without dataset scan, upload, or download behavior.

**Architecture:** Keep transport entry points in `apps/api` and `apps/cli`; put framework-free health
orchestration in `robolake/application`; put SQLAlchemy, S3, settings, and synthetic filesystem
adapters in `robolake/infrastructure`. Docker Compose supplies PostgreSQL, MinIO, bucket bootstrap,
and the API. The API health endpoint invokes real dependency probes and returns 503 when either
dependency is unavailable.

**Tech Stack:** Python 3.12, uv, FastAPI, Typer, Pydantic Settings, SQLAlchemy 2, psycopg 3, Alembic,
boto3 S3 API, PostgreSQL 17, MinIO, pytest, Ruff, mypy, Docker Compose.

**Repository rule:** Do not commit. The user must explicitly request any commit.

**Execution status:** Complete on 2026-07-10; no commit created.

---

## File map

- `pyproject.toml`, `uv.lock`: package metadata, runtime/dev dependencies, entry points, tool rules.
- `apps/api/`: FastAPI composition and HTTP-only schemas/routes.
- `apps/cli/`: Typer composition and commands.
- `robolake/domain/`: empty M0 domain boundary.
- `robolake/application/health.py`: framework-free health use case and probe protocol.
- `robolake/infrastructure/settings.py`: environment configuration.
- `robolake/infrastructure/database.py`: SQLAlchemy engine and health probe.
- `robolake/infrastructure/object_storage.py`: S3 client and health probe.
- `robolake/infrastructure/example_dataset.py`: deterministic synthetic fixture generator.
- `alembic.ini`, `migrations/`: environment-driven migration framework and empty baseline revision.
- `docker-compose.yml`, `Dockerfile`, `.dockerignore`, and
  `config/minio/robolake-policy.template.json`: local services.
- `.env.example`: synthetic-only local settings and credentials.
- `tests/unit/`: application, CLI, generator, settings, and import-boundary tests.
- `tests/integration/`: real PostgreSQL, MinIO, migration, and health endpoint tests.
- `Makefile`, `README.md`: exact setup and verification commands.

### Task 1: Toolchain and package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `apps/__init__.py`, `apps/api/__init__.py`, `apps/cli/__init__.py`
- Create: `robolake/__init__.py`, `robolake/domain/__init__.py`
- Create: `robolake/application/__init__.py`, `robolake/infrastructure/__init__.py`
- Delete after migration: `src/`
- Create: `uv.lock`

- [x] **Step 1: Install pinned uv without modifying shell configuration**

Run:

```bash
curl -LsSf https://astral.sh/uv/0.11.28/install.sh \
  | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
uv --version
```

Expected: `uv 0.11.28`.

- [x] **Step 2: Define runtime and development dependencies**

Use top-level packages so the required command `uv run mypy robolake` addresses a real directory.
Declare these entry points:

```toml
[project.scripts]
robolake = "apps.cli.main:app"

[tool.fastapi]
entrypoint = "apps.api.main:app"
```

Runtime dependencies cover FastAPI, Typer, Pydantic Settings, SQLAlchemy, Alembic, psycopg, and
boto3. The default `dev` dependency group covers pytest, HTTPX, Ruff, mypy, coverage, and boto3 S3
stubs. Configure strict mypy, Ruff line length 100/Python 3.12, and explicit `unit`/`integration`
pytest markers.

- [x] **Step 3: Create only package boundary files and sync**

Run:

```bash
uv sync
```

Expected: `.venv` and `uv.lock` are created and the editable `robolake` command is installed.

### Task 2: Framework-free health service (TDD)

**Files:**
- Create: `tests/unit/application/test_health.py`
- Create: `robolake/application/health.py`

- [x] **Step 1: Write failing service tests**

Tests define one passing and one raising probe and require:

```python
report = HealthService([PassingProbe("database"), PassingProbe("object_storage")]).check()
assert report.status == "ok"
assert report.components == {
    "application": "ok",
    "database": "ok",
    "object_storage": "ok",
}
```

A second test requires one failure to return `status == "degraded"` while still checking all probes
and never exposing exception text.

- [x] **Step 2: Run RED**

Run: `uv run pytest tests/unit/application/test_health.py -q`

Expected: failure because `HealthService` does not exist.

- [x] **Step 3: Implement minimal typed service**

Define `HealthProbe` protocol with `name` and `check() -> None`, immutable `HealthReport`, and
`HealthService.check()`. Catch `Exception` only here, the application boundary translating dependency
failures into component state.

- [x] **Step 4: Run GREEN**

Run: `uv run pytest tests/unit/application/test_health.py -q`

Expected: all health-service tests pass.

### Task 3: Settings and dependency probes (TDD)

**Files:**
- Create: `tests/unit/infrastructure/test_settings.py`
- Create: `tests/unit/infrastructure/test_database.py`
- Create: `tests/unit/infrastructure/test_object_storage.py`
- Create: `robolake/infrastructure/settings.py`
- Create: `robolake/infrastructure/database.py`
- Create: `robolake/infrastructure/object_storage.py`

- [x] **Step 1: Write failing settings tests**

Require `Settings()` to read `ROBOLAKE_DATABASE_URL`, S3 endpoint, access key, `SecretStr` secret,
bucket, and region. Verify defaults point to Compose host ports without containing production data.

- [x] **Step 2: Run settings RED**

Run: `uv run pytest tests/unit/infrastructure/test_settings.py -q`

Expected: failure because settings are absent.

- [x] **Step 3: Implement settings and run GREEN**

Use `BaseSettings` with `env_prefix="ROBOLAKE_"`, `.env` loading, ignored unknown values, and frozen
instances. Run the same test; expect pass.

- [x] **Step 4: Write failing probe tests**

Database test supplies a fake engine/context/connection and asserts `SELECT 1` is executed. Object
storage test supplies a typed fake client and asserts `head_bucket(Bucket="robolake-blobs")`.

- [x] **Step 5: Run probe RED**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_database.py \
  tests/unit/infrastructure/test_object_storage.py -q
```

Expected: failure because probe classes are absent.

- [x] **Step 6: Implement probes and run GREEN**

Create SQLAlchemy engine/session helpers and `DatabaseHealthProbe`; create a path-style SigV4 S3
client and `ObjectStorageHealthProbe`. Do not catch provider exceptions in adapters. Repeat the prior
command; expect pass.

### Task 4: FastAPI health endpoint (TDD)

**Files:**
- Create: `tests/unit/apps/api/test_system_routes.py`
- Create: `apps/api/main.py`, `apps/api/router.py`
- Create: `apps/api/routes/__init__.py`, `apps/api/routes/system.py`
- Create: `apps/api/schemas/__init__.py`, `apps/api/schemas/system.py`
- Delete after replacement: `tests/test_system_routes.py`, `tests/conftest.py`

- [x] **Step 1: Write failing route tests**

Build the app with a fake `HealthService`. Require `/` to identify RoboLake, `/health` to return 200
with application/database/object-storage all `ok`, and a failing probe to return 503 with that
component `down` and no exception detail.

- [x] **Step 2: Run RED**

Run: `uv run pytest tests/unit/apps/api/test_system_routes.py -q`

Expected: failure because the new app factory/routes are absent.

- [x] **Step 3: Implement minimal API composition**

`create_app(health_service: HealthService | None = None)` creates real SQLAlchemy/S3 resources only
when a service is not injected, stores the service on app state, includes the system router, and
closes owned clients during lifespan shutdown. Response schemas expose only stable states.

- [x] **Step 4: Run GREEN**

Run: `uv run pytest tests/unit/apps/api/test_system_routes.py -q`

Expected: route tests pass with 200/503 behavior.

### Task 5: Typer CLI and synthetic generator (TDD)

**Files:**
- Create: `tests/unit/apps/cli/test_cli.py`
- Create: `tests/unit/infrastructure/test_example_dataset.py`
- Create: `apps/cli/main.py`
- Create: `robolake/infrastructure/example_dataset.py`

- [x] **Step 1: Write failing generator tests**

Require two runs with the same seed to create identical nested synthetic files, require a non-empty
destination to raise `DestinationNotEmptyError`, and require no absolute path or credential content.

- [x] **Step 2: Run generator RED**

Run: `uv run pytest tests/unit/infrastructure/test_example_dataset.py -q`

Expected: failure because generator is absent.

- [x] **Step 3: Implement generator and run GREEN**

Generate deterministic bytes from SHA-256 of `(seed, logical path, block counter)` plus a small
synthetic CSV and README. Keep all data invented and bounded. Repeat the prior test; expect pass.

- [x] **Step 4: Write failing CLI tests**

Require root `--help`, `version`, and `example generate DESTINATION --seed 7`. Verify useful help,
exit 0, generated files, and a nonzero safe error for a non-empty destination.

- [x] **Step 5: Run CLI RED**

Run: `uv run pytest tests/unit/apps/cli/test_cli.py -q`

Expected: failure because Typer app is absent.

- [x] **Step 6: Implement CLI and run GREEN**

Use a root Typer app with an `example` sub-app. Do not add push, pull, scan, or upload commands.
Repeat the CLI test; expect pass.

### Task 6: Alembic and Compose integration skeleton

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`
- Create: `migrations/versions/20260710_0001_initial.py`
- Create: `.env.example`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`
- Create: `config/minio/robolake-policy.template.json`
- Create: `tests/integration/test_dependencies.py`
- Create: `tests/integration/test_health.py`

- [x] **Step 1: Add configuration-only migration and Compose files**

The initial migration is intentionally schema-empty; Alembic creates only its version table. Compose
uses pinned PostgreSQL/MinIO image digests, creates an unversioned `robolake-blobs` bucket and a
least-privilege API user, and maps host ports 15432/18000/19000/19001. `.env.example` contains only
synthetic local credentials.

- [x] **Step 2: Start dependencies and apply migrations**

Run:

```bash
docker compose up -d
uv run alembic upgrade head
```

Expected: services become healthy and Alembic reaches revision `20260710_0001`.

- [x] **Step 3: Write real integration tests**

Require PostgreSQL `SELECT 1`, MinIO `HeadBucket`, multipart create/abort under the blob prefix, and
an in-process `/health` response with every component `ok`.

- [x] **Step 4: Run integration GREEN**

Run: `uv run pytest tests/integration -q`

Expected: all integration tests pass against Compose services; no skip or fake backend.

### Task 7: Import boundaries, tasks, and documentation

**Files:**
- Create: `tests/unit/test_architecture.py`
- Create: `Makefile`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE_V0_1.md`
- Modify: `docs/adr/0005-modular-monolith.md`

- [x] **Step 1: Write failing import-boundary test**

Parse all `robolake/domain` and `robolake/application` Python files with `ast`; fail on imports whose
top-level package is `fastapi`, `typer`, `sqlalchemy`, `boto3`, `botocore`, or `minio`.

- [x] **Step 2: Run RED then make package layout compliant**

Run: `uv run pytest tests/unit/test_architecture.py -q` before deleting `src/`; expect a layout
assertion failure. Remove the obsolete `src` tree and old tests, update architecture paths to the
approved top-level layout, rerun, and expect pass.

- [x] **Step 3: Add exact task commands and quickstart**

Make targets wrap `uv sync`, Compose up/down, Alembic upgrade, API, CLI, unit/integration tests,
Ruff, mypy, formatting, and the full check. README starts with the required command sequence and
documents ports, health semantics, environment overrides, synthetic generator, and M0 non-goals.

### Task 8: Full M0 verification

**Files:**
- Verify all changed files; do not commit.

- [x] **Step 1: Run every required command exactly**

```bash
docker compose up -d
uv sync
uv run alembic upgrade head
uv run robolake --help
uv run pytest
uv run ruff check .
uv run mypy robolake
```

Expected: every command exits 0 without skipped integration checks or hidden failures.

- [x] **Step 2: Run formatting and operational checks**

```bash
uv run ruff format --check .
docker compose ps
curl --fail http://localhost:18000/health
make check
git diff --check
```

Expected: formatting/checks pass, all services are healthy, health reports all dependencies `ok`,
and Git reports no whitespace errors.

- [x] **Step 3: Confirm scope**

Search production code and dependencies for push/pull, authentication, Kafka, Airflow, Iceberg,
ROS2, frontend, and training implementations. Explicit documentation of non-goals is allowed;
runtime inclusion is not. Report repository tree, files, commands, results, deviations, limitations,
and exact M1 scope. Do not commit.
