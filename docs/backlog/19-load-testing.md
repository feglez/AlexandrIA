---
title: "[US] Load test the platform and measure its elasticity"
milestone: "Hito 5 - Observability & Release"
labels: "user-story,infra,docs"
---
## User story

**As an** operator,
**I want** to know how many concurrent members the Raspberry Pi can serve and how the platform behaves under overload,
**so that** I can set realistic quotas and autoscaling limits.

**Affected components:** librarian, watchman, infra

## Acceptance criteria

- [ ] **Given** a load-test script, **when** it simulates concurrent users searching and submitting files against the public endpoint, **then** it reports RPS, p95 and p99 latency and error rate.
- [ ] **Given** sustained load on search, **when** CPU crosses the HPA threshold, **then** librarian scales out and the scaling events are documented.
- [ ] **Given** overload, **when** the node saturates, **then** the bottleneck and the behaviour (latency growth, 5xx errors) are analysed in the README.

## Technical tasks

- [ ] Load-test script (k6 or Locust)
- [ ] HorizontalPodAutoscaler for the stateless services
- [ ] Run scenarios (baseline, ramp-up, spike) and collect results
- [ ] Results analysis in the README

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
