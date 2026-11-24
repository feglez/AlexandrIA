from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """The process is up and serving requests (used by the container health check)."""
    return {"status": "ok"}
