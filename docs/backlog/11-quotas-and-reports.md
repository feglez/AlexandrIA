---
title: "[US] Manage contribution quotas and monthly usage reports"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,observer,watchman"
---
## User story

**As a** library curator,
**I want** to set per-member contribution quotas and get a monthly usage report,
**so that** the limited disk of the NAS is shared fairly and running costs can be split by actual usage.

**Affected components:** observer, watchman

## Acceptance criteria

- [ ] **Given** I am a curator, **when** I `PUT /api/v1/quotas/{user}` with byte and monthly-submission limits, **then** the quota is stored and applies to the next submission.
- [ ] **Given** a submission that would exceed the member's quota, **when** watchman asks observer (`POST /internal/quota-checks`), **then** observer answers `allowed: false` with the remaining budget, and watchman rejects the submission.
- [ ] **Given** observer is unavailable, **when** watchman performs the quota check, **then** the submission goes to `pending_review` (fail-safe) instead of being auto-approved.
- [ ] **Given** a closed month, **when** I call `GET /api/v1/reports/{YYYY-MM}`, **then** I receive, for every member, storage, contributions, searches and their share of the monthly running cost.
- [ ] **Given** the internal quota endpoint, **when** it is called from outside the cluster, **then** it is not reachable.

## Technical tasks

- [ ] Quota model and curator endpoints
- [ ] Internal quota-check endpoint (service token, cluster-only)
- [ ] Monthly aggregation job and cost-split calculation (configurable monthly cost)
- [ ] Watchman client with timeout and fail-safe behaviour
- [ ] Contract test between watchman and observer

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
