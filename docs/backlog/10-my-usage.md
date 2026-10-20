---
title: "[US] See my storage usage and quota"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,observer"
---
## User story

**As a** library member,
**I want** to see how much storage I use and how much of my library contribution quota is left,
**so that** I know whether my next submission will fit before I upload it.

**Affected components:** observer, nextcloud

## Acceptance criteria

- [ ] **Given** valid credentials, **when** I call `GET /api/v1/usage/me`, **then** I see my personal Nextcloud storage (used / quota), my bytes contributed to the shared library, my submissions this month and the remaining contribution quota.
- [ ] **Given** a file is published or removed from the library, **when** observer consumes the event, **then** my contributed bytes are updated within 60 seconds.
- [ ] **Given** I request another user's usage, **when** I am not a curator, **then** I receive `403`.

## Technical tasks

- [ ] FastAPI project skeleton for observer
- [ ] Usage ledger schema (append-only events + per-period aggregates)
- [ ] Redis Streams consumer for `file.published`, `file.removed`, `submission.created`, `search.performed`, `question.answered`
- [ ] Nextcloud OCS client to read personal storage usage
- [ ] `GET /usage/me` endpoint and tests

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
