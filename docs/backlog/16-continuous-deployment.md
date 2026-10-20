---
title: "[US] Deploy automatically with zero-downtime updates"
milestone: "Hito 4 - CD & Security"
labels: "user-story,ci-cd,infra"
---
## User story

**As an** operator,
**I want** every successful CI run on `main` to be deployed to the Raspberry Pi automatically after a drift audit,
**so that** releasing a change takes no manual steps and never interrupts the members.

**Affected components:** ci-cd, infra

## Acceptance criteria

- [ ] **Given** a successful CI run on `main`, **when** the CD workflow starts, **then** it first runs `terraform plan -detailed-exitcode` and stops the deployment if drift is detected.
- [ ] **Given** no drift, **when** the new images are deployed, **then** a rolling update replaces the pods without failed requests (readiness gates, `maxUnavailable: 0`).
- [ ] **Given** the deployment credentials, **when** they are reviewed, **then** they live only in GitHub Secrets and are bound to a Kubernetes ServiceAccount restricted to the application namespaces.
- [ ] **Given** the CD runner setup, **when** a pull request comes from a fork, **then** it cannot execute jobs with access to the cluster.

## Technical tasks

- [ ] Decide how GitHub Actions reaches the Pi (self-hosted runner or private network)
- [ ] `.github/workflows/cd.yml` triggered by CI success
- [ ] Drift audit job
- [ ] Least-privilege RBAC Role/RoleBinding for the deployer
- [ ] Rolling update strategy and readiness probes

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
