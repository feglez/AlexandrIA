---
title: "[US] Detect and reconcile configuration drift"
milestone: "Hito 3 - IaC & Reconciler"
labels: "user-story,infra,docs"
---
## User story

**As an** operator,
**I want** Terraform to detect and undo manual changes made to the cluster,
**so that** the running platform always matches the configuration stored in Git.

**Affected components:** infra

## Acceptance criteria

- [ ] **Given** the applied desired state, **when** I change a resource manually (e.g. delete a NetworkPolicy rule or change a replica count with `kubectl`), **then** `terraform plan` reports the exact difference.
- [ ] **Given** detected drift, **when** I run `terraform apply`, **then** the resource is restored to the declared state.
- [ ] **Given** the experiment, **when** it is documented, **then** the README contains screenshots and command output for the four phases (desired state, drift, observation, reconciliation).
- [ ] **Given** the end of a lab session, **when** I run `terraform destroy`, **then** the managed resources are removed and the evidence is documented.

## Technical tasks

- [ ] Script the drift experiment steps
- [ ] Capture outputs and screenshots
- [ ] Document the reconciler pattern experiment
- [ ] Document the teardown procedure

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
