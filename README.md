# findmetalk-api

The API for FindMeTalk: a FastAPI modular monolith that serves the web app
(`findmetalk-web`) and the moderation console (`findmetalk-admin`), and
publishes the API contract both of them generate their types from.

Agents and contributors: read [AGENTS.md](AGENTS.md) first.

## Run it locally

Needs Python 3.12 and Docker.

```sh
make install              # .venv with runtime and dev dependencies
cp .env.example .env      # local settings; never committed
make up                   # Postgres 16 (host port 5434) and Redis 7 (6381)
.venv/bin/alembic upgrade head
make run                  # http://localhost:8000/docs
make down                 # stop the containers when done
```

## Checks

`make check` runs what CI runs: ruff, mypy, the contract check, unit and
integration tests, and the migration round trip. It needs `make up`.

| Command | What it does |
|---------|--------------|
| `make test` | Unit tests only (no services needed) |
| `make test-integration` | All tests, against local Postgres and Redis |
| `make migrate-check` | upgrade, `alembic check`, downgrade, upgrade |
| `make contract` | Fails if `contract/` is out of date |
| `python -m api.contract.export` | Regenerates `contract/` |

## Layout

```
main.py                 app, middleware, lifespan
api/core/               health probes, request context, Redis client
api/db/                 engine, sessions, database URL handling
api/utils/              settings, logging
api/contract/           OpenAPI and WebSocket event schema export
api/v1/routes/<module>/     HTTP routes
api/v1/services/<module>/   business logic
api/v1/models/<module>/     SQLAlchemy models
api/v1/schemas/<module>/    Pydantic schemas
alembic/                migrations
contract/               the published contract (generated, committed)
tests/                  unit tests; tests/integration/ needs real services
```

## Probes

- `GET /health`: liveness. 200 whenever the process is serving.
- `GET /ready`: readiness. 200 when Postgres and Redis answer, 503 with the
  failed dependency named otherwise.

Every response carries an `X-Request-ID`, which also appears on every log line
for that request. Logs are JSON on stdout and carry no PII.

## Deploying

Migrations are applied as a separate step before the new code deploys
(`alembic upgrade head` against the target database); starting a container
never runs them. Settings come from the environment; `.env.example` lists
every one with what it gates.
