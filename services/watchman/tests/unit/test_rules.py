"""The rule engine: status of a submission once its checks have run."""

import pytest

from watchman.checks import QUOTA_SKIPPED, Check
from watchman.policy import DEFAULT_POLICY, AutoApprove, Policy
from watchman.rules import status_after_checks

LIMIT = DEFAULT_POLICY.auto_approve.max_size_bytes
CHECKS = ("size_limit", "mime_consistency", "blocked_type", "duplicate", "quota")
MEMBER = {"members"}
TRUSTED = {"members", "trusted"}


def results(**overrides: str) -> list[Check]:
    """Every check passes, except the ones given."""
    checks = [Check(name, overrides.get(name, "pass")) for name in CHECKS]
    if any(result == "fail" for result in overrides.values()) and "quota" not in overrides:
        checks[-1] = QUOTA_SKIPPED
    return checks


def auto_approve(groups: list[str], max_size_bytes: int = LIMIT) -> Policy:
    rule = AutoApprove(groups=groups, max_size_bytes=max_size_bytes)
    return DEFAULT_POLICY.model_copy(update={"auto_approve": rule})


@pytest.mark.parametrize(
    ("policy", "groups", "size_bytes", "checks", "status"),
    [
        (DEFAULT_POLICY, TRUSTED, 1000, results(), "approved"),
        (DEFAULT_POLICY, {"trusted"}, LIMIT, results(), "approved"),
        (DEFAULT_POLICY, TRUSTED, LIMIT + 1, results(), "pending_review"),
        (DEFAULT_POLICY, MEMBER, 1000, results(), "pending_review"),
        (DEFAULT_POLICY, set(), 1000, results(), "pending_review"),
        (DEFAULT_POLICY, TRUSTED, 1000, results(quota="unavailable"), "pending_review"),
        (DEFAULT_POLICY, MEMBER, 1000, results(quota="unavailable"), "pending_review"),
        (DEFAULT_POLICY, TRUSTED, 1000, results(quota="fail"), "rejected"),
        (DEFAULT_POLICY, TRUSTED, 1000, results(duplicate="fail"), "rejected"),
        (DEFAULT_POLICY, TRUSTED, LIMIT * 10, results(size_limit="fail"), "rejected"),
        (DEFAULT_POLICY, MEMBER, 1000, results(mime_consistency="fail"), "rejected"),
        (DEFAULT_POLICY, MEMBER, 1000, results(blocked_type="fail"), "rejected"),
        (auto_approve([]), TRUSTED, 1000, results(), "pending_review"),
        (auto_approve(["members"]), MEMBER, 1000, results(), "approved"),
        (auto_approve(["parents", "trusted"]), TRUSTED, 1000, results(), "approved"),
        (auto_approve(["trusted"], max_size_bytes=999), TRUSTED, 1000, results(), "pending_review"),
    ],
    ids=[
        "trusted",
        "only-trusted-at-the-limit",
        "trusted-over-the-limit",
        "member",
        "no-groups",
        "trusted-quota-unavailable",
        "member-quota-unavailable",
        "trusted-quota-exceeded",
        "trusted-duplicate",
        "trusted-too-large",
        "member-mime-mismatch",
        "member-blocked-type",
        "rule-without-groups",
        "rule-for-members",
        "rule-with-two-groups",
        "rule-with-a-smaller-limit",
    ],
)
def test_status_after_checks(
    policy: Policy, groups: set[str], size_bytes: int, checks: list[Check], status: str
) -> None:
    assert status_after_checks(policy, frozenset(groups), size_bytes, checks) == status
