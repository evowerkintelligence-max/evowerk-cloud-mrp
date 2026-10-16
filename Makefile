# One entry point for a fresh clone. CI runs the same commands
# (.github/workflows/ci.yml). Windows without make: run the recipe lines
# by hand from the same directories.

.PHONY: install test lint typecheck build up lock

install:
	cd backend && uv sync --frozen
	cd frontend && npm ci

test:
	cd backend && uv run pytest -q
	cd frontend && npm test

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint

typecheck:
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build
	docker compose build

up:
	docker compose up --build

# Regenerate backend/uv.lock after editing backend/pyproject.toml.
lock:
	cd backend && uv lock
