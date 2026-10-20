---
title: "[US] Submit a file to the shared library"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,watchman"
---
## User story

**As a** library member,
**I want** to submit a file to the shared library,
**so that** it can be added to the household's curated collection after it passes the checks.

**Affected components:** watchman, nextcloud, observer

## Acceptance criteria

- [ ] **Given** valid Nextcloud credentials, **when** I `POST /api/v1/submissions` with a file and a target collection, **then** I receive `202 Accepted` with the submission id and its status.
- [ ] **Given** a new submission, **when** it is stored, **then** it lands in the quarantine area and is **not** visible in the shared library.
- [ ] **Given** a request without valid credentials, **when** it reaches the watchman, **then** it is rejected with `401`.
- [ ] **Given** a file larger than the policy limit, or whose real MIME type does not match its extension, **when** it is checked, **then** it is auto-rejected with a machine-readable reason.
- [ ] **Given** a file whose SHA-256 matches a file already in the library, **when** it is checked, **then** it is auto-rejected as a duplicate and the response references the existing file.
- [ ] **Given** a member over their contribution quota, **when** they submit, **then** the submission is rejected with reason `quota_exceeded`.

## Technical tasks

- [ ] FastAPI project skeleton for watchman (settings, logging, error handlers)
- [ ] Nextcloud auth dependency (validate app passwords with `GET /ocs/v2.php/cloud/user`, cache user and groups in Redis)
- [ ] WebDAV client: upload to the quarantine folder
- [ ] Submission model and migrations (PostgreSQL)
- [ ] Automatic checks: size, MIME sniffing, blocked types, SHA-256 duplicate lookup
- [ ] Quota check call to observer (`POST /internal/quota-checks`)
- [ ] Publish `submission.created` event on the Redis stream
- [ ] Unit tests for checks; integration test with a Nextcloud container

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
