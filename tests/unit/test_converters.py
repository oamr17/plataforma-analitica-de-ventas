"""Interpretación estricta, exacta y reutilizable del contrato de origen."""

from datetime import date
from decimal import Decimal

import pytest

from sales_analytics.converters import parse_date, parse_decimal, parse_integer


@pytest.mark.parametrize(
    "text,result", [("001", 1), (" 11 ", 11), ("0", 0), ("-1", -1)]
)
def test_integer(text, result):
    assert parse_integer(text) == result


@pytest.mark.parametrize("text", ["", "NA", "1.5", "1e2", "1,000", "１", "2147483648"])
def test_invalid_integer(text):
    with pytest.raises(ValueError):
        parse_integer(text)


def test_bigint_and_dates():
    assert parse_integer("2147483648", bits=64) == 2147483648
    assert parse_date("2/29/2020") == date(2020, 2, 29)


@pytest.mark.parametrize("text", ["2/29/2021", "31/1/2020", "", "NA", "2020-01-01"])
def test_invalid_date(text):
    with pytest.raises(ValueError):
        parse_date(text)


def test_decimal_is_exact():
    assert parse_decimal(" $1,234.50 ", scale=2, money=True) == Decimal("1234.50")
    assert parse_decimal("0.12345678", scale=8) == Decimal("0.12345678")
    assert parse_decimal("-1.00", scale=2, money=True) == Decimal("-1.00")


@pytest.mark.parametrize(
    "text",
    [
        "NaN",
        "Infinity",
        "1e3",
        "$1,23.00",
        "1.001",
        "",
        "NA",
        "$1.00 USD",
        "10000000000000000",
    ],
)
def test_invalid_money(text):
    with pytest.raises(ValueError):
        parse_decimal(text, scale=2, money=True)
