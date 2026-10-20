---
title: "[US] Ask questions about the library and get answers with sources"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,librarian"
---
## User story

**As a** library member,
**I want** to ask questions about the library's documents in natural language and get an answer that cites its sources,
**so that** I get the information I need without opening and reading every document.

**Affected components:** librarian

## Acceptance criteria

- [ ] **Given** indexed documents, **when** I `POST /api/v1/questions` with a question, **then** I receive `202 Accepted` with the question id and the status `queued`.
- [ ] **Given** a queued question, **when** it is processed, **then** the most relevant passages are retrieved, the LLM runtime writes an answer based only on them, and `GET /api/v1/questions/{id}` returns the answer with its sources (path, snippet and link to each file).
- [ ] **Given** a question that the library's documents cannot answer, **when** it is processed, **then** the answer says that the information was not found.
- [ ] **Given** several questions sent at the same time, **when** they are processed, **then** they are answered one at a time in arrival order, and the rest of the platform keeps responding.
- [ ] **Given** a file that is still in quarantine or was rejected, **when** a question is answered, **then** it is never used as a source.
- [ ] **Given** a question asked by another member, **when** I request it, **then** I receive `404`.

## Technical tasks

- [ ] Deploy the LLM runtime (Ollama) with a small multilingual instruct model
- [ ] Question queue and a single background worker
- [ ] Retrieval step reusing the semantic search
- [ ] Prompt template that restricts the answer to the retrieved passages and asks for citations
- [ ] `POST /questions` and `GET /questions/{id}` endpoints
- [ ] Record `question.answered` usage events for observer
- [ ] Tests with a fixture corpus and a stubbed LLM

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
