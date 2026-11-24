"""Split of the monthly running cost among the members (showback, README 5.4).

Each member pays a share of the cost proportional to their storage: their
personal storage in Nextcloud plus their contribution to the library. The
amounts are rounded to cents, and whatever the rounding leaves over (or
takes too much) goes to the member with the largest storage, the first by
user name on a tie, so the amounts add up to the cost exactly. If nobody
stores anything, the cost is split equally.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
SHARE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class Share:
    # Fraction of the cost, with four decimal places.
    share: Decimal
    # Euros, with two decimal places.
    amount: Decimal


def split_cost(cost: Decimal, storage: Mapping[str, int]) -> dict[str, Share]:
    """The share and the amount of each member, from their storage in bytes."""
    if any(size < 0 for size in storage.values()):
        raise ValueError("storage cannot be negative")
    members = sorted(storage)
    if not members:
        return {}
    total = sum(storage.values())
    fractions = {
        member: Decimal(storage[member]) / total if total else Decimal(1) / len(members)
        for member in members
    }
    amounts = {
        member: (cost * fraction).quantize(CENT, ROUND_HALF_UP)
        for member, fraction in fractions.items()
    }
    largest = max(members, key=lambda member: storage[member])
    amounts[largest] += cost - sum(amounts.values())
    return {
        member: Share(fractions[member].quantize(SHARE_PLACES, ROUND_HALF_UP), amounts[member])
        for member in members
    }
