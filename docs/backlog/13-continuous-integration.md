---
title: "[US] Validate every change and publish multi-architecture images"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,ci-cd"
---
## User story

**As a** developer,
**I want** every push and pull request to be linted, tested and packaged automatically,
**so that** broken code never reaches `main` and the Raspberry Pi always has an image it can run.

**Affected components:** ci-cd

## Acceptance criteria

- [ ] **Given** a push or PR to `main`, **when** the CI workflow runs, **then** it runs Python linting, Dockerfile linting, unit tests and integration tests.
- [ ] **Given** a failing check, **when** the workflow finishes, **then** the PR cannot be merged.
- [ ] **Given** a successful run on `main`, **when** images are built, **then** they are published to GitHub Container Registry for `linux/arm64` (and `linux/amd64`), tagged with the commit SHA and, on tags, the semantic version.

## Technical tasks

- [ ] `.github/workflows/ci.yml` triggered on `push` and `pull_request` to `main`
- [ ] Lint jobs (ruff, hadolint)
- [ ] Test job with service containers (PostgreSQL, Redis)
- [ ] Multi-arch build and push to GHCR
- [ ] Required status checks in the `main` ruleset

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
