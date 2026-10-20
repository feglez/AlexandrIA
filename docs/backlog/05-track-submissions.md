---
title: "[US] Track my submissions and get notified"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,watchman"
---
## User story

**As a** library member,
**I want** to follow the status of my submissions and be notified when a curator decides,
**so that** I don't have to keep checking whether my file was accepted.

**Affected components:** watchman, nextcloud

## Acceptance criteria

- [ ] **Given** I have submissions, **when** I call `GET /api/v1/submissions?mine=true`, **then** I only see my own submissions with their current status.
- [ ] **Given** one of my submissions, **when** I call `GET /api/v1/submissions/{id}`, **then** I see its full status history and the check results.
- [ ] **Given** a curator approves or rejects my submission, **when** the decision is stored, **then** I receive a notification in Nextcloud with the outcome (and the reason, if rejected).
- [ ] **Given** a submission still pending review, **when** I `DELETE` it, **then** it is withdrawn and the quarantined file is removed.
- [ ] **Given** a submission that was already decided, **when** I try to withdraw it, **then** I receive `409 Conflict`.

## Technical tasks

- [ ] List/detail endpoints with ownership filtering and pagination
- [ ] Status history table
- [ ] Nextcloud notifications client
- [ ] Withdraw endpoint and state transition
- [ ] Tests for ownership rules and invalid transitions

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
