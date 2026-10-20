# Rules for coding agents

Every agent working in this repository (Codex, Gemini, Claude, or any other)
follows these rules. Read the whole file before you change code. `GEMINI.md`
and `CLAUDE.md` point here; this file is the only copy of the rules.

## How work flows here

- **Implementers** (Codex, Gemini) build features and open pull requests.
- **The reviewer** (Claude) reviews every pull request. Fixes for review
  findings land as new commits on the same branch.
- **The reviewer** also merges the pull requests it approves into
  `development`.
- **Isaac** owns the product and is the only person who merges into `main`.

## Read the spec first

The product spec lives at `findmetalk-docs/findMeTalk_SPEC.md`. It is on the
owner's machine only. Read it before any task, and follow section 0:

- **`OPEN` means undecided. Stop and ask the owner.** Never pick a default for
  an OPEN item, not even "for now". The list is in spec section 17.
- `PROPOSED` means build behind an abstraction so it can be swapped.
- `DECIDED` means build it; do not re-argue it.
- Every requirement has an ID (`FR-CALL-3`, `TR-3`, `SAFE-2`, `REC-4`).
  Name the IDs in branch names, commits, pull requests and test docstrings.
- Safety (spec section 11) overrides convenience and speed. If a task
  conflicts with section 11, stop and tell Isaac instead of building it.
- If the spec does not cover something, ask. Never guess.

## Private docs never reach GitHub

This repository is **public**. `findmetalk-docs/` (the spec, ADRs, notes) is
gitignored and must never be committed, quoted at length in a pull request,
or copied into code comments. CI fails if anything under it is tracked.
Do not `git add -f` it. Do not move the spec into the repository.

## Branches, commits and pull requests

- `development` is the integration branch; `main` is the release branch.
- Start every task on a new branch from `origin/development`, named
  `<type>/<slice-or-id>-<short-name>`, e.g. `feat/fr-auth-1-email-otp`.
  Open the pull request into `development`.
- **Never push to `development` or `main`. Never merge a pull request.**
  The reviewer merges approved pull requests into `development`; Isaac
  merges `development` into `main`.
- Never reuse a branch that was already merged.
- **One change per pull request**: one slice, or one part of one (spec 15).
  Never bundle auth, safety, recording or call-state changes with other work.
- Commit as `theijhay <olawaleisaacjohn@gmail.com>` (set `user.name` and
  `user.email` before the first commit). **No AI attribution anywhere**: no
  `Co-Authored-By`, no "Generated with ...", no agent or model name, in
  commits, pull requests, comments or code. After opening a pull request,
  read its body back and remove any footer that appeared.

## Pull requests are public

This repository is public and is read by people outside the project. Keep
every pull request plain and professional:

- The body follows the template and states **what was done**: summary,
  changes, migration, testing. Straightforward, no padding.
- **No questions, open items, notes to the owner, or TODO lists** in the
  pull request body, and **no comments** on your own pull request.
- Questions, risks and decisions that need Isaac go to him directly in your
  session, before you open the pull request, never on GitHub.
- Nothing about accounts, billing, credentials or internal tooling.

## Before every push: `make ci`

`make ci` runs every gate in `.github/workflows/ci.yml` on your machine except
the Docker image build: ruff, mypy (strict), the contract check, all tests
including integration tests against real Postgres and Redis, the migration
round trip, the dependency audit and gitleaks over the full history. It starts
the containers it needs and stops them again, pass or fail. **A pull request
that changes `Dockerfile`, `requirements.txt` or `.dockerignore` runs
`make ci-image` instead**, which also builds the image and checks it runs as a
non-root user. **A pull request is only opened or pushed after its gate
passes**, and the Testing section of the pull request says which one ran. If
anything fails, do not push: fix it, or report the failure to Isaac with the
real output.

While iterating, `make up` then `make check` is the quicker subset. `make
down` when you are done: shut down every container you start.

Never weaken a gate to get green: no `# type: ignore` or `# noqa` without a
comment saying why, no skipped or deleted tests, no loosened CI.

## Architecture (spec 10)

- One FastAPI service, a **modular monolith**. Modules: `auth`, `profiles`,
  `presence`, `calls`, `circles`, `safety`, `recordings`, `media_tokens`,
  `admin`. Adding a module is an architecture change: ask first.
- Layout, by layer then module:
  - routes in `api/v1/routes/<module>/`
  - business logic in `api/v1/services/<module>/`
  - SQLAlchemy models in `api/v1/models/<module>/`
  - Pydantic schemas in `api/v1/schemas/<module>/`
  - shared infrastructure in `api/core/`, `api/db/`, `api/utils/`
- **Modules talk through services and domain events, never each other's
  tables.** Module A may import `api.v1.services.B` and `api.v1.schemas.B`,
  never `api.v1.models.B` or `api.v1.routes.B`.
  `tests/test_module_boundaries.py` enforces this.
- Absolute imports only.
- Routes stay thin: validate, call a service, return a typed schema with
  `response_model`. Business rules live in services.
- Call and circle state transitions are validated server-side and idempotent
  (TR-2). Clients never decide state.
- Presence lives in Redis with a TTL; Postgres is the source of truth for
  everything durable (TR-1).
- All LiveKit access goes through one `MediaProvider` interface (10.4).
- Every safety-state mutation (block, report, suspend, recording access)
  writes to the append-only audit log in the same transaction (TR-7).

## The API contract (TR-8)

The web and admin repos generate their types from `contract/openapi.json` and
`contract/events.schema.json`. After changing a route, schema or WebSocket
event, run `python -m api.contract.export` and commit the result. CI fails on
drift. Every route declares `response_model`; WebSocket events are registered
in `api/contract/events.py`. A breaking change is listed under Changes in the pull
request.

Each feature issue defines its endpoints, request and response bodies, status
codes, error codes and events. Implement them as written. If one cannot be
built as specified, ask Isaac before changing the shape.

## API conventions

- **Base path** `/api/v1`. JSON request and response bodies, `snake_case`
  field names.
- **Ids** are UUID strings. **Timestamps** are ISO 8601 in UTC
  (`2026-10-19T21:04:05Z`). **Durations** are integers with the unit in the
  name (`expires_in_seconds`).
- **Auth**: `Authorization: Bearer <access_token>` on every route except the
  sign-in routes and probes. The API's callers are the web and admin
  **servers** (their route handlers proxy every browser request), so sign-in
  and refresh return both tokens in the response body to them; they keep the
  tokens in `HttpOnly` cookies on their own origin, and browser JavaScript
  never sees one. The browser opens the WebSocket with a short-lived,
  single-use ticket, never the access token.
- **Success bodies** are the resource itself, never wrapped. Every route
  declares a `response_model`.
- **Status codes**: `200` read or update with a body, `201` created (body is
  the new resource), `202` accepted for later processing, `204` success with
  no body. `401` not signed in, `403` signed in but not allowed, `404` not
  found or not visible to this user, `409` conflicts with current state,
  `422` invalid input, `429` too many requests (with `Retry-After`).
- **Errors** always have this shape. `code` is a stable, namespaced key the
  apps translate (`auth.code_invalid`); it is never an English sentence.
  ```json
  {
    "error": {
      "code": "validation_failed",
      "fields": [{ "field": "display_name", "code": "profile.display_name_length" }],
      "request_id": "3f9c2a7e5b1d4c8e9a0b6d2f4e1c7a95"
    }
  }
  ```
  `fields` appears only on `422`.
- **Lists** are cursor-paginated: query `?cursor=<opaque>&limit=<1..100>`
  (default 50), response `{ "items": [...], "next_cursor": "..." | null }`.
- **Never leak what the spec hides.** A skipped, expired or guarded call
  request, a blocked user and a full circle all return the same neutral
  response as "not available". Sign-in responses are identical whether or
  not the email has an account.
- **Actions** on a resource are `POST /resource/{id}/<verb>` and are
  idempotent: repeating one that already happened returns the same result.
- **WebSocket events** use the envelope
  `{ "event": "<name>", "v": 1, "id": "<uuid, client events only>", "data": { ... } }`
  and are registered in `api/contract/events.py`.

## Database and migrations

- A schema change needs an Alembic revision in `alembic/versions/`, and new
  models are imported in `api/v1/models/__init__.py`.
- `alembic check` runs in CI with server defaults compared: a column the
  model expects Postgres to fill must get its default in the migration.
- Every migration has a working `downgrade()`; CI runs the round trip.
- Never edit, delete or regenerate a migration that is on `development`. Add
  a new revision on the current head (`alembic heads` on `origin/development`).
- A migration runs before the new code deploys, so it must work with the code
  already running: never drop or rename something the running code uses.
- Never run migrations or scripts against a deployed database. Local Docker
  Postgres only (`make up`, host port 5434).

## Configuration

- Every setting lives in `api/utils/settings.py`. **A new setting is added to
  `.env.example` in the same pull request**, with a comment saying what it
  gates and how to generate it if it is a secret, and listed in the PR
  description so it reaches the deployment.
- Thresholds the spec says are configurable (guardrails in FR-CALL-5, ring
  timeout, circle size, retention days, per-country recording switches) are
  config values, never constants in code.
- The product name is `settings.APP_NAME`. Never write it into code or copy.
- Never commit `.env`, keys or credentials, not even in tests.

## Tests

- Unit tests fake Postgres and Redis; they prove nothing about SQL or Redis
  semantics. Anything touching a query, a lock, a TTL or a transaction gets an
  integration test in `tests/integration/` against real services.
- State machines (calls, circles, reports) get unit tests for every
  transition, including the illegal ones.
- A test for a bug must fail without the fix. Check that it does.
- Name tests for the behaviour they prove, and cite the requirement ID in the
  docstring.

## Privacy and safety rules that are easy to break

- **No PII in logs**: no email addresses, codes, IP addresses, display
  names, request bodies or query strings. Log ids and outcomes. Use
  `api.utils.logger.redact` for anything secret that must appear at all.
- No text chat, DMs or file sharing between users, ever (P1).
- Skip and timeout must be indistinguishable to the caller (FR-CALL-4).
- Guardrails are invisible: never return a message like "you reached a
  limit" (FR-CALL-5).
- No bots, fake users or fake counts in the cloud (9.2).
- Recording rules REC-1 to REC-9 are never weakened without an owner decision.
- No crisis hotline numbers in code (CR-4).
- Copy touching crisis, health, recording consent or legal topics is marked
  `NEEDS REVIEW`. Never present policy text as legally reviewed.
- User-facing strings returned by the API are stable keys the clients
  translate, not English sentences.

## Style

- Match the surrounding code: naming, comment density, idiom.
- Comments say why, not what.
- **No em dashes**, in copy, messages or comments. Use a comma, colon or full
  stop.
- Scalable, not complicated: no abstraction that is not earned yet.
