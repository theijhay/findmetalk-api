# The same gates CI runs. `make check` must pass before every push.

PY := .venv/bin/python
TEST_DB_URL ?= postgresql://findmetalk:findmetalk@localhost:5434/findmetalk_test
TEST_REDIS_URL ?= redis://localhost:6381/15

.PHONY: install lint format typecheck test test-integration contract migrate-check check up down run

install:
	python3.12 -m venv .venv
	$(PY) -m pip install -q -r requirements-dev.txt

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

typecheck:
	$(PY) -m mypy

# Unit tests only; integration tests skip without TEST_DB_URL/TEST_REDIS_URL.
test:
	$(PY) -m pytest -q

# Needs `make up`. Runs every test, integration included, and fails rather
# than skip if the services are missing.
test-integration:
	TEST_DB_URL=$(TEST_DB_URL) TEST_REDIS_URL=$(TEST_REDIS_URL) REQUIRE_INTEGRATION=1 $(PY) -m pytest -q

contract:
	$(PY) -m api.contract.export --check

# Migrations apply cleanly, match the models, and reverse cleanly. Needs `make up`.
migrate-check:
	DB_URL=$(TEST_DB_URL) REDIS_URL=$(TEST_REDIS_URL) $(PY) -m alembic upgrade head
	DB_URL=$(TEST_DB_URL) REDIS_URL=$(TEST_REDIS_URL) $(PY) -m alembic check
	DB_URL=$(TEST_DB_URL) REDIS_URL=$(TEST_REDIS_URL) $(PY) -m alembic downgrade base
	DB_URL=$(TEST_DB_URL) REDIS_URL=$(TEST_REDIS_URL) $(PY) -m alembic upgrade head

check: lint typecheck contract test-integration migrate-check

up:
	docker compose up -d --wait postgres redis

down:
	docker compose down

run:
	$(PY) main.py
