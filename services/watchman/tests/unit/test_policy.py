"""Validation of the moderation policy document (PUT /api/v1/policies)."""

import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from watchman.policy import DEFAULT_POLICY, Policy

README = Path(__file__).parents[4] / "README.md"


def document(**changes: object) -> dict:
    return DEFAULT_POLICY.model_dump() | changes


def test_default_policy_is_the_example_of_the_api_specification() -> None:
    if not README.exists():
        pytest.skip("README.md is not next to the service (only in the repository)")
    match = re.search(r"`PUT /policies` - request:\s*```json\n(.*?)```", README.read_text(), re.S)
    assert match is not None

    assert Policy.model_validate(json.loads(match.group(1))) == DEFAULT_POLICY


def test_types_and_extensions_are_stored_in_lowercase() -> None:
    policy = Policy.model_validate(
        document(allowed_mime_types=[" Application/PDF", "TEXT/*"], blocked_extensions=[".EXE "])
    )

    assert policy.allowed_mime_types == ["application/pdf", "text/*"]
    assert policy.blocked_extensions == [".exe"]


def test_auto_approve_can_be_turned_off() -> None:
    off = document(auto_approve={"groups": [], "max_size_bytes": 1})

    assert Policy.model_validate(off).auto_approve.groups == []


def without(field: str) -> dict:
    data = document()
    del data[field]
    return data


def auto_approve(**changes: object) -> dict:
    data = copy.deepcopy(document())
    data["auto_approve"] |= changes
    return data


@pytest.mark.parametrize(
    ("data", "location", "error"),
    [
        (without("auto_approve"), ("auto_approve",), "missing"),
        (document(max_size=10), ("max_size",), "extra_forbidden"),
        (document(max_size_bytes=0), ("max_size_bytes",), "greater_than"),
        (document(max_size_bytes="1000"), ("max_size_bytes",), "int_type"),
        (document(allowed_mime_types="text/*"), ("allowed_mime_types",), "list_type"),
        (
            document(allowed_mime_types=["pdf"]),
            ("allowed_mime_types", 0),
            "value_error",
        ),
        (
            document(allowed_mime_types=["*/*"]),
            ("allowed_mime_types", 0),
            "value_error",
        ),
        (
            document(blocked_extensions=["exe"]),
            ("blocked_extensions", 0),
            "value_error",
        ),
        (
            document(blocked_extensions=[".tar.gz"]),
            ("blocked_extensions", 0),
            "value_error",
        ),
        (auto_approve(groups=[" "]), ("auto_approve", "groups", 0), "string_too_short"),
        (auto_approve(max_size_bytes=-1), ("auto_approve", "max_size_bytes"), "greater_than"),
        (auto_approve(admins=True), ("auto_approve", "admins"), "extra_forbidden"),
        (auto_approve(max_size_bytes=DEFAULT_POLICY.max_size_bytes + 1), (), "value_error"),
    ],
    ids=[
        "missing-field",
        "unknown-field",
        "zero-size",
        "size-as-text",
        "types-not-a-list",
        "type-without-subtype",
        "any-type",
        "extension-without-dot",
        "two-extensions",
        "blank-group",
        "negative-auto-approve-size",
        "unknown-auto-approve-field",
        "auto-approve-over-the-limit",
    ],
)
def test_invalid_documents(data: dict, location: tuple, error: str) -> None:
    with pytest.raises(ValidationError) as info:
        Policy.model_validate(data)

    [found] = info.value.errors()
    assert (found["loc"], found["type"]) == (location, error)
