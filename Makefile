# The same gates CI runs. `make ci` runs all of them and must pass before
# every push; `make check` is the quick subset (needs `make up`).

PY := .venv/bin/python
TEST_DB_URL ?= postgresql://findmetalk:findmetalk@localhost:5434/findmetalk_test
TEST_REDIS_URL ?= redis://localhost:6381/15

.PHONY: install lint format typecheck test test-integration contract migrate-check check audit secrets image ci up down run

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

# Known-vulnerable dependencies (the CI `dependency audit` job).
audit:
	$(PY) -m pip_audit -r requirements.txt -r requirements-dev.txt --progress-spinner off

# Secrets anywhere in git history (the CI `secret scan` job).
secrets:
	docker run --rm -v "$(CURDIR):/repo" zricethezav/gitleaks:v8.24.3 git /repo --redact --no-banner --exit-code 1

# The image builds and runs as a non-root user (the CI `docker image` job).
image:
	docker build -t findmetalk-api:local .
	docker run --rm -e DB_URL=postgresql://u:p@127.0.0.1:1/d -e REDIS_URL=redis://127.0.0.1:1/0 \
		findmetalk-api:local sh -c 'test "$$(id -u)" != 0 && python -c "import main"'

# Every gate .github/workflows/ci.yml runs, locally. Starts Postgres and
# Redis, and always stops them again, pass or fail.
ci:
	$(MAKE) up
	$(MAKE) check audit secrets image; status=$$?; $(MAKE) down; exit $$status

up:
	docker compose up -d --wait postgres redis

down:
	docker compose down

run:
	$(PY) main.py
