---
title: "[US] Health checks and structured logs"
milestone: "Hito 5 - Observability & Release"
labels: "user-story,watchman,librarian,observer"
---
## User story

**As an** operator,
**I want** each service to report its health and to log in a structured format,
**so that** Kubernetes can restart broken pods and I can diagnose problems quickly.

**Affected components:** watchman, librarian, observer

## Acceptance criteria

- [ ] **Given** a running service, **when** I call `GET /health`, **then** it reports the status of PostgreSQL, Redis and Nextcloud (and `503` if a critical dependency is down).
- [ ] **Given** the Kubernetes manifests, **when** they are applied, **then** liveness and readiness probes use the health endpoints.
- [ ] **Given** any request, **when** it is logged, **then** the log line is JSON on stdout with timestamp, level, service, request id, route, status code and latency.

## Technical tasks

- [ ] `/health` endpoint with dependency checks in each service
- [ ] JSON logging configuration and request-id middleware
- [ ] Probes in the manifests

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
