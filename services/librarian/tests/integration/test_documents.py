"""GET /api/v1/documents, POST /api/v1/reindex and GET /api/v1/reindex/{id}."""

from datetime import datetime

import httpx
import pytest

from librarian.indexer import Indexer
from librarian.reconcile import reconcile
from librarian.resources import Resources
from tests.integration.conftest import (
    ALICE,
    CAROL,
    PUBLIC_BASE_URL,
    FakeLibrary,
    index,
    index_all,
)

pytestmark = pytest.mark.usefixtures("mocked")


async def test_curators_list_the_documents_by_path(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    invoice = await index(resources, "Library/Invoices/power.md", "Power.", "Supply.")
    await index(resources, "Library/Manuals/washer.md", "Washer.")
    await index(resources, "Library/Manuals/broken.pdf", status="failed")

    response = await client.get("/api/v1/documents", auth=CAROL)

    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 20, 0)
    assert [item["path"] for item in page["items"]] == [
        "Library/Invoices/power.md",
        "Library/Manuals/broken.pdf",
        "Library/Manuals/washer.md",
    ]
    first = page["items"][0]
    assert first == {
        "id": invoice.id,
        "path": "Library/Invoices/power.md",
        "collection": "Invoices",
        "status": "indexed",
        "chunks": 2,
        "indexed_at": first["indexed_at"],
        "error": None,
        "attempts": 0,
        "next_attempt_at": None,
        "url": f"{PUBLIC_BASE_URL}/f/{invoice.file_id}",
    }
    assert datetime.fromisoformat(first["indexed_at"]).tzinfo is not None


async def test_failed_documents_show_the_error_and_the_next_attempt(
    client: httpx.AsyncClient, resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    library.add("Library/Manuals/broken.pdf", b"%PDF-1.4\nbroken\n")
    await index(resources, "Library/Manuals/washer.md", "Washer.")
    # The reconciliation finds the new file; the indexer cannot read it.
    await reconcile(resources)
    await index_all(indexer)

    response = await client.get("/api/v1/documents", params={"status": "failed"}, auth=CAROL)

    page = response.json()
    assert page["total"] == 1
    failed = page["items"][0]
    assert failed["path"] == "Library/Manuals/broken.pdf"
    assert failed["error"].startswith("Cannot read the file as pdf")
    assert failed["attempts"] == 1
    assert failed["next_attempt_at"] is not None


async def test_the_list_can_be_filtered_and_paged(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    for number in range(5):
        await index(resources, f"Library/Manuals/manual-{number}.md", "Manual.")
    await index(resources, "Library/Invoices/power.md", "Power.")
    await index(resources, "Library/Manuals/scan.png", status="skipped")

    response = await client.get(
        "/api/v1/documents",
        params={"collection": "Manuals", "status": "indexed", "limit": 2, "offset": 2},
        auth=CAROL,
    )

    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 2)
    assert [item["path"] for item in page["items"]] == [
        "Library/Manuals/manual-2.md",
        "Library/Manuals/manual-3.md",
    ]


@pytest.mark.parametrize(
    "params",
    [{"status": "done"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"collection": " "}],
)
async def test_invalid_filters_are_refused(
    client: httpx.AsyncClient, params: dict[str, object]
) -> None:
    response = await client.get("/api/v1/documents", params=params, auth=CAROL)

    assert response.status_code == 422
    assert response.json()["errors"]


async def test_members_cannot_list_the_documents(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/documents", auth=ALICE)).status_code == 403
    assert (await client.get("/api/v1/documents")).status_code == 401


async def test_members_can_read_one_document(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    document = await index(resources, "Library/Manuals/washer.md", "Washer.", "Warranty.")

    response = await client.get(f"/api/v1/documents/{document.id}", auth=ALICE)

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["path"], body["collection"]) == (
        document.id,
        "Library/Manuals/washer.md",
        "Manuals",
    )
    assert (body["status"], body["chunks"]) == ("indexed", 2)
    assert body["indexed_at"] is not None


async def test_an_unknown_document_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/documents/doc_01JB9A0C2D", auth=ALICE)

    assert response.status_code == 404
    assert response.json()["detail"] == "There is no such document in the index."


async def test_a_curator_starts_a_reindex_and_follows_it(
    client: httpx.AsyncClient, resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    document = await index(resources, "Library/Manuals/washer.md", "Washer.")
    library.files["Library/Manuals/washer.md"] = (document.file_id, b"Washer, again.")

    response = await client.post("/api/v1/reindex", json={"collection": "Manuals"}, auth=CAROL)

    assert response.status_code == 202
    job = response.json()
    assert job["job_id"].startswith("job_")
    assert job == {
        "job_id": job["job_id"],
        "status": "queued",
        "collection": "Manuals",
        "requested_by": "carol",
        "created_at": job["created_at"],
        "started_at": None,
        "finished_at": None,
        "documents": None,
        "pending": 0,
        "error": None,
    }
    assert resources.indexer_wakeup.is_set()

    await indexer.start_next_job()
    running = (await client.get(f"/api/v1/reindex/{job['job_id']}", auth=CAROL)).json()
    assert (running["status"], running["documents"], running["pending"]) == ("running", 1, 1)
    assert running["started_at"] is not None

    await index_all(indexer)
    await indexer.finish_jobs()
    done = (await client.get(f"/api/v1/reindex/{job['job_id']}", auth=CAROL)).json()
    assert (done["status"], done["documents"], done["pending"]) == ("done", 1, 0)
    assert done["finished_at"] is not None


@pytest.mark.parametrize("body", [None, {}, {"collection": None}])
async def test_without_a_collection_the_whole_library_is_reindexed(
    client: httpx.AsyncClient, body: dict[str, object] | None
) -> None:
    response = await client.post("/api/v1/reindex", json=body, auth=CAROL)

    assert response.status_code == 202
    assert response.json()["collection"] is None


@pytest.mark.parametrize(
    "body", [{"collection": ""}, {"collection": "x" * 256}, {"everything": True}]
)
async def test_an_invalid_reindex_request_is_refused(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await client.post("/api/v1/reindex", json=body, auth=CAROL)

    assert response.status_code == 422
    assert response.json()["errors"]


async def test_members_cannot_reindex_or_follow_jobs(client: httpx.AsyncClient) -> None:
    job = (await client.post("/api/v1/reindex", auth=CAROL)).json()

    assert (await client.post("/api/v1/reindex", auth=ALICE)).status_code == 403
    assert (await client.get(f"/api/v1/reindex/{job['job_id']}", auth=ALICE)).status_code == 403
    assert (await client.post("/api/v1/reindex")).status_code == 401


async def test_an_unknown_job_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/reindex/job_01JB9B7K", auth=CAROL)

    assert response.status_code == 404
    assert response.json()["detail"] == "There is no such re-index job."
