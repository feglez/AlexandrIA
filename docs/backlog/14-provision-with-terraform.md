---
title: "[US] Provision the private cloud with Terraform"
milestone: "Hito 3 - IaC & Reconciler"
labels: "user-story,infra"
---
## User story

**As an** operator,
**I want** the whole platform on the Raspberry Pi to be declared in Terraform,
**so that** I can recreate it from scratch and every change to it goes through a pull request.

**Affected components:** infra

## Acceptance criteria

- [ ] **Given** the Terraform code, **when** I run `terraform apply`, **then** it creates the namespaces, network policies (default deny plus explicit allows), persistent volume claims, deployments, services and ingress.
- [ ] **Given** the code layout, **when** it is reviewed, **then** it is split into `main.tf`, `variables.tf`, `outputs.tf` and `providers.tf` and organised into modules (network, compute, persistence).
- [ ] **Given** two concurrent `terraform apply` runs, **when** the second one starts, **then** it is blocked by the remote state lock.
- [ ] **Given** the repository, **when** it is scanned, **then** no `.tfvars`, kubeconfig or credentials are committed.

## Technical tasks

- [ ] Install K3s on the Raspberry Pi (without Traefik) with storage on the USB SSD
- [ ] Install the Tailscale Kubernetes operator (ingress exposed with Funnel)
- [ ] Remote backend with state locking
- [ ] Network module (namespaces, NetworkPolicies, ingress)
- [ ] Persistence module (PVCs, PostgreSQL, Redis)
- [ ] Compute module (Nextcloud and service deployments)
- [ ] Variables and outputs documentation

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
