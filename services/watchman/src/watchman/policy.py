"""Moderation policy: the limits the automatic checks apply to new submissions."""

from typing import Annotated

from fastapi import Depends
from pydantic import BaseModel, Field

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class Policy(BaseModel):
    """The defaults are the example policy of the API specification (README 5.2)."""

    max_size_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    # Exact types, or "type/*" for every subtype.
    allowed_mime_types: list[str] = ["application/pdf", "text/*", "image/jpeg", "image/png", DOCX]
    blocked_extensions: list[str] = [".exe", ".msi", ".bat", ".sh", ".apk"]


async def active_policy() -> Policy:
    return Policy()


ActivePolicy = Annotated[Policy, Depends(active_policy)]
