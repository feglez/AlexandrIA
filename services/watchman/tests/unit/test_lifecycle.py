import itertools

import pytest

from watchman.db.models import SUBMISSION_STATUSES
from watchman.lifecycle import TRANSITIONS, InvalidTransition, can_change, check_transition

# The arrows of the state diagram in README section 1.5.
ALLOWED = {
    ("checking", "approved"),
    ("checking", "pending_review"),
    ("checking", "rejected"),
    ("pending_review", "approved"),
    ("pending_review", "rejected"),
    ("pending_review", "withdrawn"),
    ("approved", "published"),
    ("approved", "failed"),
    ("failed", "approved"),
}
FORBIDDEN = sorted(set(itertools.product(SUBMISSION_STATUSES, repeat=2)) - ALLOWED)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transition(current: str, target: str) -> None:
    assert can_change(current, target)
    check_transition(current, target)


@pytest.mark.parametrize(("current", "target"), FORBIDDEN)
def test_forbidden_transition(current: str, target: str) -> None:
    assert not can_change(current, target)
    with pytest.raises(InvalidTransition) as error:
        check_transition(current, target)
    assert (error.value.current, error.value.target) == (current, target)


def test_every_status_is_covered() -> None:
    assert set(TRANSITIONS) == set(SUBMISSION_STATUSES)
    assert len(ALLOWED) + len(FORBIDDEN) == len(SUBMISSION_STATUSES) ** 2 == 49
