---
title: "[US] Review pending submissions"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,watchman"
---
## User story

**As a** library curator,
**I want** to approve or reject pending submissions,
**so that** only relevant, safe and non-duplicated files reach the shared library.

**Affected components:** watchman, nextcloud

## Acceptance criteria

- [ ] **Given** I belong to the curators group, **when** I list submissions with `status=pending_review`, **then** I see each file's submitter, size, type, check results and note.
- [ ] **Given** a pending submission, **when** I approve it, **then** the file is moved server-side (WebDAV `MOVE`) from quarantine to the target collection and becomes visible to all members.
- [ ] **Given** a pending submission, **when** I reject it, **then** a reason is mandatory and the quarantined file is deleted.
- [ ] **Given** a user who is not a curator, **when** they try to approve or reject, **then** they receive `403`.
- [ ] **Given** any decision, **when** it is stored, **then** the decision, the curator and the timestamp are recorded in the submission history.
- [ ] **Given** an approved submission, **when** it is published, **then** a `file.published` event is emitted.

## Technical tasks

- [ ] Submission state machine with allowed transitions and tests
- [ ] `POST /submissions/{id}/approve` and `/reject` endpoints
- [ ] Curator role check (Nextcloud group membership)
- [ ] WebDAV `MOVE` and `DELETE` operations with error handling and retries
- [ ] Emit `file.published` / `submission.rejected` events
- [ ] Integration tests for approve and reject flows

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
