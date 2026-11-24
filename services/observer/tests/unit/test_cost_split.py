from decimal import Decimal

import pytest

from observer.cost_split import Share, split_cost


def test_the_readme_example() -> None:
    # README section 5.4: 5.40 euros between alice and bob.
    shares = split_cost(Decimal("5.40"), {"alice": 9_609_989_324, "bob": 11_281_069_056})

    assert shares == {
        "alice": Share(Decimal("0.4600"), Decimal("2.48")),
        "bob": Share(Decimal("0.5400"), Decimal("2.92")),
    }


def test_the_rounding_difference_goes_to_the_largest_share() -> None:
    shares = split_cost(Decimal("1.00"), {"alice": 1, "bob": 1, "carol": 2})

    # 0.25 + 0.25 + 0.50 adds up; with three equal parts it would not.
    assert [shares[m].amount for m in ("alice", "bob", "carol")] == [
        Decimal("0.25"),
        Decimal("0.25"),
        Decimal("0.50"),
    ]
    shares = split_cost(Decimal("10.00"), {"bob": 7, "alice": 7, "carol": 7})
    # A tie: the first by user name gets the extra cent.
    assert [shares[m].amount for m in ("alice", "bob", "carol")] == [
        Decimal("3.34"),
        Decimal("3.33"),
        Decimal("3.33"),
    ]
    shares = split_cost(Decimal("0.02"), {"alice": 1, "bob": 1, "carol": 1})
    # Rounding up took too much: the largest share gives it back.
    assert [shares[m].amount for m in ("alice", "bob", "carol")] == [
        Decimal("0.00"),
        Decimal("0.01"),
        Decimal("0.01"),
    ]


@pytest.mark.parametrize(
    "storage",
    [
        {"alice": 1, "bob": 2, "carol": 3, "dave": 5},
        {"alice": 10**12, "bob": 1, "carol": 0},
        {"alice": 123_456_789, "bob": 987_654_321, "carol": 555_555_555},
        {f"member{n}": n * 7919 for n in range(1, 30)},
    ],
)
@pytest.mark.parametrize("cost", ["5.40", "0.01", "99.99", "1234.56"])
def test_the_amounts_add_up_to_the_cost(storage: dict[str, int], cost: str) -> None:
    shares = split_cost(Decimal(cost), storage)

    assert sum(share.amount for share in shares.values()) == Decimal(cost)
    assert all(share.amount >= 0 for share in shares.values())
    assert all(share.amount == share.amount.quantize(Decimal("0.01")) for share in shares.values())


def test_without_storage_the_cost_is_split_equally() -> None:
    assert split_cost(Decimal("5.40"), {"alice": 0, "bob": 0}) == {
        "alice": Share(Decimal("0.5000"), Decimal("2.70")),
        "bob": Share(Decimal("0.5000"), Decimal("2.70")),
    }


def test_a_member_without_storage_pays_nothing() -> None:
    shares = split_cost(Decimal("5.40"), {"alice": 0, "bob": 300})

    assert shares["alice"] == Share(Decimal("0.0000"), Decimal("0.00"))
    assert shares["bob"] == Share(Decimal("1.0000"), Decimal("5.40"))


def test_no_members_and_no_cost() -> None:
    assert split_cost(Decimal("5.40"), {}) == {}
    assert split_cost(Decimal("0"), {"alice": 5})["alice"].amount == 0


def test_storage_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        split_cost(Decimal("5.40"), {"alice": -1, "bob": 2})
