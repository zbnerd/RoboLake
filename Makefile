UV ?= uv

.PHONY: api check cli demo down example format format-check integration lint migrate ps sync test typecheck unit up

sync:
	$(UV) sync

up:
	docker compose up -d --wait

down:
	docker compose down

ps:
	docker compose ps

migrate:
	$(UV) run alembic upgrade head

api:
	$(UV) run fastapi dev

cli:
	$(UV) run robolake --help

test:
	$(UV) run pytest

unit:
	$(UV) run pytest -m unit

integration: up migrate
	$(UV) run pytest -m integration

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

format-check:
	$(UV) run ruff format --check .

typecheck:
	$(UV) run mypy robolake apps

check: lint format-check typecheck test

example:
	$(UV) run robolake example generate examples/synthetic-dataset --seed 7

demo:
	scripts/demo-v01.sh
