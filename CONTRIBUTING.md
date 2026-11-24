# Contributing guide

This document defines the engineering workflow used throughout the project: branching strategy, commit conventions, and how work flows from a user story to `main`.

## Branching strategy

The project follows a **trunk-based workflow with short-lived feature branches**.

- `main` is the only long-lived branch and must always be in a working state.
- Direct pushes to `main` are not allowed. All changes reach `main` through a **pull request** that uses the [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- Work happens in short-lived branches created from the latest `main`. They are deleted after merging.

### Branch naming

`<type>/<issue-number>-<short-description>`

| Prefix      | Use                                               | Example                               |
|-------------|---------------------------------------------------|---------------------------------------|
| `feat/`     | New functionality                                 | `feat/4-submission-review`            |
| `fix/`      | Bug fixes                                         | `fix/3-duplicate-hash-check`          |
| `docs/`     | Documentation only                                | `docs/1-architecture-diagram`         |
| `refactor/` | Internal changes without behaviour change         | `refactor/4-nextcloud-client`         |
| `test/`     | Tests only                                        | `test/7-search-integration`           |
| `chore/`    | Maintenance (dependencies, configuration)         | `chore/bump-fastapi`                  |
| `build/`    | Container images, compose, build configuration    | `build/12-local-environment`          |

### Merge policy

- PRs are merged with **squash merge**, so each PR becomes one Conventional Commit on `main`.
- A PR must reference the user story it implements (`Closes #<n>`).

Repository settings (*Settings → Rules → Rulesets* for `main`): require a pull request before merging, block force pushes, and allow only squash merges.

## Commit convention

Commits follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<optional scope>): <short imperative summary>

[optional body: what and why, not how]

[optional footer: Closes #12, BREAKING CHANGE: ...]
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build` (Dockerfiles, `compose.yaml` and build configuration).

**Scopes:** `watchman`, `librarian`, `observer`, `nextcloud`, `docs`.

Examples:

```
feat(watchman): add SHA-256 duplicate detection to submission checks
fix(librarian): skip empty chunks when indexing scanned PDFs
docs: add NIST SP 500-292 actor mapping
chore: add pull request template
build(librarian): bake the embedding model into the image
```

## From user story to `main`

1. Pick (or create) a GitHub Issue using the **User story** template. It must have the `user-story` label, acceptance criteria, a checklist of technical tasks, and a milestone (Hito 1–5).
2. Create a branch following the naming convention above.
3. Commit using Conventional Commits; tick the issue checklist as tasks are completed.
4. Open a pull request with the PR template and `Closes #<n>`.
5. Once the checklist is complete, squash-merge and delete the branch.

## Local development

**Tools:** Docker with Docker Compose, and [uv](https://docs.astral.sh/uv/) for the Python projects. The lock files were made with uv 0.13.9, the version used to build the images; after adding or updating a dependency, relock with that version so that `uv sync --locked` keeps working in the Dockerfiles.

**Layout:** each service under `services/` is an independent uv project with its own `pyproject.toml`, `uv.lock`, Dockerfile and tests. Some modules are copies in the three services (`config.py`, `problems.py`, `auth.py`, `resources.py`, `db/` and the tests' `conftest.py`), so a fix in one of them must be applied to the three.

**Whole platform:** `cp .env.example .env && docker compose up -d --wait`. See [README section 7](README.md#7-local-environment).

**Linters**, from the repository root:

```bash
uvx ruff@0.17.6 check services
uvx ruff@0.17.6 format --check services
for svc in watchman librarian observer; do
  docker run --rm -i hadolint/hadolint:v2.15.1 < services/$svc/Dockerfile
done
```

**Tests.** Unit tests need nothing else. Integration tests need PostgreSQL with pgvector and Redis, and the ones in the compose stack are not reachable from the host, so start two throwaway containers on other ports (PostgreSQL needs a few seconds before it accepts connections):

```bash
docker run -d --rm --name test-pg -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:0.8.7-pg17-trixie
docker run -d --rm --name test-redis -p 127.0.0.1:56379:6379 redis:8.8.4-alpine

cd services/watchman   # or librarian, observer
TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@127.0.0.1:55432/postgres \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 uv run pytest

docker stop test-pg test-redis
```

Without `TEST_DATABASE_URL` and `TEST_REDIS_URL` the integration tests are skipped (they fail instead when the `CI` variable is set). Each run recreates the database `test_<service>`. Nextcloud is never called for real in these tests: its answers are mocked with respx.
