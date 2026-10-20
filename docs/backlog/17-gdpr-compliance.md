---
title: "[US] Keep all personal data under GDPR jurisdiction"
milestone: "Hito 4 - CD & Security"
labels: "user-story,infra,docs"
---
## User story

**As a** library member,
**I want** my files and personal data to stay on hardware in the EU that the household controls,
**so that** my data is protected under the GDPR and not handed to a third-party cloud provider.

**Affected components:** infra, docs

## Acceptance criteria

- [ ] **Given** the deployment, **when** it is audited, **then** every persistent volume and database lives on the Raspberry Pi located in Spain.
- [ ] **Given** the documentation, **when** it is reviewed, **then** it lists the personal data stored by each service, its purpose and its retention period.
- [ ] **Given** the public endpoint, **when** it is accessed, **then** traffic is encrypted with TLS all the way to the cluster (the Tailscale Funnel relays cannot decrypt it).
- [ ] **Given** any external dependency (Tailscale, container registry), **when** it is documented, **then** it states what data it can see and where it is processed.

## Technical tasks

- [ ] Data inventory per service
- [ ] TLS configuration for the public endpoint
- [ ] GDPR section in the README

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
