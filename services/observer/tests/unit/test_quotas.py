import pytest

from observer.quotas import Quota, QuotaCheck, check

QUOTA = Quota(max_contribution_bytes=1000, max_submissions_per_month=3)


@pytest.mark.parametrize(
    ("contributed", "submissions", "size", "expected"),
    [
        (0, 0, 100, QuotaCheck(True, 900, 2)),
        # The file fits exactly, and it is the last submission of the month.
        (400, 2, 600, QuotaCheck(True, 0, 0)),
        (0, 0, 0, QuotaCheck(True, 1000, 2)),
        # Not allowed: the figures are what is left now.
        (400, 0, 601, QuotaCheck(False, 600, 3)),
        (0, 3, 1, QuotaCheck(False, 1000, 0)),
        (999, 3, 2, QuotaCheck(False, 1, 0)),
        # A quota lowered below what the member already has: nothing is left.
        (1500, 5, 1, QuotaCheck(False, 0, 0)),
    ],
)
def test_check(contributed: int, submissions: int, size: int, expected: QuotaCheck) -> None:
    assert check(QUOTA, contributed, submissions, size) == expected


def test_a_quota_of_zero_allows_nothing() -> None:
    quota = Quota(max_contribution_bytes=0, max_submissions_per_month=0)

    assert check(quota, 0, 0, 0) == QuotaCheck(False, 0, 0)
