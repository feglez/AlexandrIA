"""Life cycle of a submission (README section 1.5): the allowed status changes."""

TRANSITIONS: dict[str, frozenset[str]] = {
    "checking": frozenset({"pending_review", "approved", "rejected"}),
    "pending_review": frozenset({"approved", "rejected", "withdrawn"}),
    "approved": frozenset({"published", "failed"}),
    # A curator approves a failed submission again to retry the move.
    "failed": frozenset({"approved"}),
    "published": frozenset(),
    "rejected": frozenset(),
    "withdrawn": frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"A submission cannot go from {current} to {target}.")
        self.current = current
        self.target = target


def can_change(current: str, target: str) -> bool:
    return target in TRANSITIONS[current]


def check_transition(current: str, target: str) -> None:
    if not can_change(current, target):
        raise InvalidTransition(current, target)
