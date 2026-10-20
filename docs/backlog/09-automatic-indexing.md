---
title: "[US] Index library changes automatically"
milestone: "Hito 2 - Packaging & CI"
labels: "user-story,librarian"
---
## User story

**As a** library member,
**I want** newly published files to become searchable automatically, and removed files to disappear from search,
**so that** search results always reflect the real content of the library.

**Affected components:** librarian, watchman, nextcloud

## Acceptance criteria

- [ ] **Given** a `file.published` event, **when** librarian consumes it, **then** the file is downloaded via WebDAV, its text extracted, chunked, embedded and searchable within 60 seconds (for documents under 5 MB).
- [ ] **Given** PDF, DOCX, Markdown or plain-text files, **when** they are indexed, **then** their text is extracted; unsupported types are recorded as `skipped`.
- [ ] **Given** a file deleted or moved in the library, **when** librarian is informed, **then** its chunks are removed or updated.
- [ ] **Given** an extraction failure, **when** it happens, **then** the document is marked `failed`, retried with back-off and visible in `GET /api/v1/documents?status=failed`.
- [ ] **Given** I am a curator, **when** I call `POST /api/v1/reindex`, **then** a full re-index job starts and returns `202` with a job id.

## Technical tasks

- [ ] Redis Streams consumer group for `file.published` / `file.removed`
- [ ] Text extractors (PDF, DOCX, MD, TXT)
- [ ] Chunking strategy with overlap
- [ ] Idempotent upsert keyed by file id + content hash
- [ ] watchman: register a `webhook_listeners` listener (renamed/deleted nodes in the library) and expose `POST /internal/webhooks/nextcloud`
- [ ] Consume `file.renamed` / `file.removed` events (watchman republishes them from Nextcloud `webhook_listeners` webhooks)
- [ ] Periodic reconciliation against the library folder as a fallback for missed webhooks
- [ ] Re-index job endpoint and document status endpoints

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Code merged into `main` through a pull request that references this issue.
- [ ] CI pipeline is green.
