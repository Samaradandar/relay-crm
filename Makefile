# Shortcuts: make dev, make test, make migrate, make seed, make lint, make local.
COMPOSE = docker compose

dev:            ## whole stack in Docker
	$(COMPOSE) up --build

local:          ## whole backend WITHOUT Docker (embedded Postgres), API on :8000
	cd api && python scripts/run_local.py

infra:
	$(COMPOSE) up -d postgres minio minio-init mailpit

test:           ## backend tests need a real Postgres: embedded by default, or TEST_ADMIN_URL
	cd api && python -m pytest --cov=app.platform --cov-fail-under=90
	cd web && npm test

migrate:
	$(COMPOSE) run --rm api sh -c "alembic upgrade head && python -m app.platform.jobs.schema"

seed:
	$(COMPOSE) run --rm api python scripts/seed_dev.py

lint:
	cd api && ruff check . && ruff format --check . && mypy app
	cd web && npm run lint && npm run typecheck

types:          ## regenerate web/src/api/types.ts from the backend OpenAPI schema
	cd api && python scripts/export_openapi.py
	cd web && npx openapi-typescript openapi.json -o src/api/types.ts

.PHONY: dev local infra test migrate seed lint types
