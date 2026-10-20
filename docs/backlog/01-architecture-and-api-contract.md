---
title: "[US] Document the domain, architecture and API contract"
milestone: "Hito 1 - Project definition"
labels: "user-story,docs"
---
## User story

**As a** developer,
**I want** the business domain, the architecture and the REST API contract documented before writing code,
**so that** each microservice can be built and tested independently against a stable contract.

**Affected components:** docs

## Acceptance criteria

- [ ] **Given** the README, **when** a reader opens it, **then** it describes the business problem, the actors and the main use cases.
- [ ] **Given** the README, **when** the NIST section is reviewed, **then** each of the 5 essential characteristics (SP 800-145) is justified against a concrete feature, including its limitations.
- [ ] **Given** the README, **when** the actors section is reviewed, **then** Consumer, Provider, Broker, Auditor and Carrier (SP 500-292) are mapped with an economic impact analysis.
- [ ] **Given** the architecture diagram, **when** it is rendered on GitHub, **then** it shows every component, the protocol of every connection and where data is stored.
- [ ] **Given** the API specification, **when** a developer reads it, **then** every endpoint lists route, HTTP verb, auth requirement and JSON payloads.

## Technical tasks

- [ ] Write domain description and use cases
- [ ] Write NIST SP 800-145 justification
- [ ] Write NIST SP 500-292 actor mapping and cost model
- [ ] Draw the architecture diagram (Mermaid)
- [ ] Write the REST API specification for watchman, librarian and observer
- [ ] Check the document against each requirement of the Hito 1 guideline

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Changes merged into `main` through a pull request that references this issue.
