---
title: "[US] Search the library in natural language"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,librarian"
---
## User story

**As a** library member,
**I want** to search the shared library with a natural-language question,
**so that** I can find a document even when I don't remember its name or the exact words it contains.

**Affected components:** librarian

## Acceptance criteria

- [ ] **Given** indexed documents, **when** I call `GET /api/v1/search?q=...`, **then** I receive the top-`k` passages, each with file path, snippet, relevance score and a link to the file in Nextcloud.
- [ ] **Given** a query that shares meaning but not keywords with a document (e.g. "electricity bill" vs. "invoice for power supply"), **when** I search, **then** that document appears in the results.
- [ ] **Given** a file that is still in quarantine or was rejected, **when** anyone searches, **then** it never appears in the results.
- [ ] **Given** an empty or too-long query, **when** I search, **then** I receive `422`.
- [ ] **Given** a filter `collection=...`, **when** I search, **then** results only come from that collection.

## Technical tasks

- [ ] FastAPI project skeleton for librarian
- [ ] PostgreSQL + pgvector schema (documents, chunks, embeddings)
- [ ] Embedding service wrapper (small ONNX model, loaded once at start-up)
- [ ] Similarity search query with optional collection filter
- [ ] Record `search.performed` usage events for observer
- [ ] Relevance tests on a small fixture corpus

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
