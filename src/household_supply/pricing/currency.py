from __future__ import annotations

from decimal import Decimal

from ..domain.money import Money
from .estimate import PriceEstimate

def convert_money(
    money: Money,
    rate: Decimal,
    target_currency: str,
) -> Money:
    """
    Convert a monetary value using an explicitly supplied exchange rate.

    The rate means:

        1 source currency = rate target currency.
    """

    if rate <= 0:
        raise ValueError("exchange rate must be greater than zero")

    if not target_currency.strip():
        raise ValueError("target_currency must not be empty")

    converted_amount = money.amount * rate

    return Money(
        converted_amount,
        target_currency,
    )


def convert_estimate(
    estimate: PriceEstimate,
    rate: Decimal,
    target_currency: str,
) -> PriceEstimate:
    """
    Convert a complete price estimate into another currency.
    """

    min_price = convert_money(
        estimate.min_price,
        rate,
        target_currency,
    )

    max_price = convert_money(
        estimate.max_price,
        rate,
        target_currency,
    )

    return PriceEstimate(
        min_price=min_price,
        max_price=max_price,
    )
