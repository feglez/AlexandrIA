---
title: "[US] Publish release v1.0.0"
milestone: "Hito 5 - Observability & Release"
labels: "user-story,docs"
---
## User story

**As a** library member,
**I want** a stable, documented v1.0.0 of the platform with a public link,
**so that** I can rely on it and others can reproduce it.

**Affected components:** docs

## Acceptance criteria

- [ ] **Given** the README, **when** it is read, **then** it includes the architecture manual, the local setup guide, the deployment guide and the link to the live application.
- [ ] **Given** the final commit, **when** the release is created, **then** a GitHub Release `v1.0.0` with release notes points to it.

## Technical tasks

- [ ] Consolidate documentation
- [ ] Changelog and release notes
- [ ] Tag and publish the release

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
