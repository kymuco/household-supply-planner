from decimal import Decimal

import pytest

from household_supply.domain.money import Money
from household_supply.pricing.estimate import PriceEstimate
from household_supply.pricing.currency import (
    convert_estimate,
    convert_money,
)



def test_convert_money() -> None:
    result = convert_money(
        Money("10", "USD"),
        Decimal("87.5"),
        "KGS",
    )

    assert result.amount == Decimal("875")
    assert result.currency == "KGS"


def test_convert_money_fractional_value() -> None:
    result = convert_money(
        Money("3.4", "USD"),
        Decimal("87.5"),
        "KGS",
    )

    assert result.amount == Decimal("297.50")
    assert result.currency == "KGS"


def test_convert_estimate() -> None:
    estimate = PriceEstimate(
        min_price=Money("3.4", "USD"),
        max_price=Money("4.6", "USD"),
    )

    result = convert_estimate(
        estimate,
        Decimal("87.5"),
        "KGS",
    )

    assert result.min_price.amount == Decimal("297.50")
    assert result.max_price.amount == Decimal("402.50")
    assert result.min_price.currency == "KGS"
    assert result.max_price.currency == "KGS"


def test_zero_amount() -> None:
    result = convert_money(
        Money("0", "USD"),
        Decimal("87.5"),
        "KGS",
    )

    assert result.amount == Decimal("0")
    assert result.currency == "KGS"


def test_negative_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_money(
            Money("10", "USD"),
            Decimal("-1"),
            "KGS",
        )


def test_zero_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_money(
            Money("10", "USD"),
            Decimal("0"),
            "KGS",
        )


def test_empty_target_currency_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert_money(
            Money("10", "USD"),
            Decimal("87.5"),
            " ",
        )
