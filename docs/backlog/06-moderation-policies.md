---
title: "[US] Configure moderation policies"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,watchman"
---
## User story

**As a** library curator,
**I want** to configure rules that approve or reject submissions automatically,
**so that** I only spend time on the submissions that really need a human decision.

**Affected components:** watchman

## Acceptance criteria

- [ ] **Given** I am a curator, **when** I `GET /api/v1/policies`, **then** I see the active policy (size limit, allowed and blocked MIME types, trusted groups, auto-approve rules).
- [ ] **Given** I am a curator, **when** I `PUT /api/v1/policies` with a valid document, **then** the policy is validated, versioned and applied to new submissions only.
- [ ] **Given** a member of a trusted group submits a file that passes every check, **when** it is processed, **then** it is auto-approved and published without curator action.
- [ ] **Given** an invalid policy document, **when** it is submitted, **then** the API responds `422` with the validation errors.
- [ ] **Given** any policy change, **when** it is saved, **then** the previous version and the author are kept for auditing.

## Technical tasks

- [ ] Policy schema (Pydantic) and versioned storage
- [ ] Rule evaluation engine used by the checking pipeline
- [ ] Policy endpoints (curator only)
- [ ] Table-driven unit tests for the rule engine

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
