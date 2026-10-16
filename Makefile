# One entry point for a fresh clone. CI runs the same commands
# (.github/workflows/ci.yml). Windows without make: run the recipe lines
# by hand from the same directories.
PYTHON ?= python3
UV_COMPILE = uv pip compile --python-platform x86_64-manylinux_2_28 --python-version 3.12 -q --custom-compile-command "make lock"

.PHONY: install test lint typecheck build up lock

install:
	cd backend && $(PYTHON) -m pip install -r requirements-dev.lock.txt
	cd frontend && npm ci

test:
	cd backend && $(PYTHON) -m pytest -q
	cd frontend && npm test

lint:
	cd backend && $(PYTHON) -m ruff check .
	cd frontend && npm run lint

typecheck:
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build
	docker compose build

up:
	docker compose up --build

# Regenerate the pinned backend lockfiles after editing backend/requirements*.txt.
lock:
	cd backend && $(UV_COMPILE) requirements.txt -o requirements.lock.txt
	cd backend && $(UV_COMPILE) requirements-dev.txt -o requirements-dev.lock.txt
