# ADR 0005: Modular Monolith

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

v0.1 needs a CLI, HTTP API, PostgreSQL, and object-storage integration, but it represents one product
workflow with strongly consistent state transitions. Splitting it into deployable services would add
network contracts, distributed transactions, operational tooling, and failure modes without an
observed scaling or ownership boundary.

The current repository is a minimal FastAPI scaffold, so boundaries can be established before
business logic accumulates.

## Decision

Build one Python 3.12 codebase with two thin entry points and four dependency layers:

```text
apps/api
apps/cli
robolake/domain
robolake/application
robolake/infrastructure
```

- `domain` owns entities, value objects, state machines, and exceptions and uses no framework.
- `application` owns use cases and ports; it may depend on `domain` only.
- `infrastructure` implements persistence, S3, configuration, clock, and hashing ports.
- `apps/api` and `apps/cli` validate transport input, invoke use cases, and render results.

FastAPI, Typer, SQLAlchemy, and the S3 SDK are forbidden in `domain` and `application`. The API is one
deployable process; the CLI is a separately installed entry point from the same package. PostgreSQL
transactions coordinate registry state. No internal HTTP call separates modules.

Boundary rules are enforced with import tests and review. Modules expose explicit interfaces and do
not reach into another layer's implementation details.

## Consequences

- Domain and application behavior can be unit-tested without PostgreSQL, MinIO, FastAPI, or Typer.
- API and CLI share use cases instead of duplicating orchestration.
- Registry transitions remain local database transactions.
- One repository, release, and migration set keeps v0.1 operation small.
- Infrastructure adapters can be replaced without changing domain contracts.
- A poorly maintained monolith can still become coupled, so import boundaries and focused modules
  are required.

## Alternatives considered

### Microservices for registry, upload, and verification

Rejected because there is no independent scaling, ownership, or release requirement. It would make
state consistency and local development harder and is an explicit v0.1 non-goal.

### Flat FastAPI application with routes calling SQLAlchemy/S3 directly

Rejected because CLI reuse, state-machine testing, and adapter replacement would require duplicated
or framework-coupled business logic.

### Separate CLI repository

Rejected because manifest and application contracts would need coordinated releases before the
workflow is stable.

## Review trigger

Revisit only when measured load, deployment isolation, fault containment, or independent team
ownership creates a concrete boundary that cannot be handled inside the monolith. A future split must
name the transaction and operational costs it accepts.

## Related documents

- [Architecture: component boundaries](../ARCHITECTURE_V0_1.md#3-system-context-and-component-boundaries)
- [ADR 0001: v0.1 scope](0001-v01-scope-and-non-goals.md)
