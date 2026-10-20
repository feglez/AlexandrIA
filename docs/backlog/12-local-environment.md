---
title: "[US] Run the whole platform locally with one command"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,ci-cd"
---
## User story

**As a** developer,
**I want** to start Nextcloud, the three services, the LLM runtime, PostgreSQL and Redis locally with a single command,
**so that** I can develop and test the integrations without touching the Raspberry Pi.

**Affected components:** watchman, librarian, observer, nextcloud, infra

## Acceptance criteria

- [ ] **Given** a fresh clone and a copy of `.env.example`, **when** I run `docker compose up -d`, **then** every container becomes healthy.
- [ ] **Given** the running stack, **when** I restart it, **then** Nextcloud files and database data persist in named volumes.
- [ ] **Given** the compose networks, **when** I inspect them, **then** PostgreSQL and Redis are only reachable from an internal network, not from the host.
- [ ] **Given** each service image, **when** I inspect it, **then** it is built multi-stage, uses a minimal base image and runs as a non-root user.

## Technical tasks

- [ ] Multi-stage `Dockerfile` per service (non-root user, cached dependency layer)
- [ ] `compose.yaml` with internal/public networks and named volumes
- [ ] Nextcloud bootstrap (service account, curators group, library and quarantine folders)
- [ ] `.env.example` without real credentials
- [ ] Health checks in compose

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
