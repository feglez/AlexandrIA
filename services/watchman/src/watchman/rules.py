"""Rule engine: the status of a new submission once its checks have run.

- A failed check rejects it.
- The policy's auto-approve rule approves it when the submitter is in one of
  the rule's groups, the file is not larger than the rule's limit and every
  check passed. A quota check that could not run (`unavailable`) is not a
  pass, so a submission is never approved automatically while observer is
  down.
- Anything else waits for a curator.
"""

from collections.abc import Collection, Sequence
from typing import Literal

from watchman.checks import Check
from watchman.policy import Policy

Status = Literal["rejected", "approved", "pending_review"]

# History reason when the policy approves a submission.
AUTO_APPROVED = "auto_approve"


def status_after_checks(
    policy: Policy, groups: Collection[str], size_bytes: int, results: Sequence[Check]
) -> Status:
    if any(check.result == "fail" for check in results):
        return "rejected"
    rule = policy.auto_approve
    if (
        any(group in groups for group in rule.groups)
        and size_bytes <= rule.max_size_bytes
        and all(check.result == "pass" for check in results)
    ):
        return "approved"
    return "pending_review"
