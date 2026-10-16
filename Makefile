# One entry point for a fresh clone. CI runs the same commands
# (.github/workflows/ci.yml). Windows without make: run the recipe lines
# by hand from the same directories.

.PHONY: install test lint typecheck build up lock

install:
	npm ci
	cd backend && uv sync --frozen

test:
	cd backend && uv run pytest -q
	npm --workspace frontend test

lint:
	cd backend && uv run ruff check .
	npm --workspace frontend run lint

typecheck:
	npm --workspace frontend run typecheck

build:
	npm --workspace frontend run build
	docker compose build

up:
	docker compose up --build

# Regenerate backend/uv.lock after editing backend/pyproject.toml.
lock:
	cd backend && uv lock
