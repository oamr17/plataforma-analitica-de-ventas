"""Transformación del candidato: expectativas independientes del cargador."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from sales_analytics import transform


def test_money_codes_online_nulls_and_source_preserved(valid_rows):
    valid_rows["Products.csv"][0]["Unit Price USD"] = " $1,234.50 "
    valid_rows["Sales.csv"][0]["Delivery Date"] = ""
    valid_rows["Customers.csv"][0]["Gender"] = " "
    before = deepcopy(valid_rows)
    candidate = transform.transform_records(valid_rows)
    assert candidate["dim_producto"][0][-2:] == (Decimal("1234.50"), Decimal("1.00"))
    assert candidate["dim_producto"][0][4:7] == ("0101", "Sub", "01")
    assert candidate["dim_cliente"][0][1] is None
    assert candidate["dim_cliente"][0][4:7] == ("NA", "Provincia", "00100")
    assert candidate["dim_sucursal"][0] == (
        0,
        "Online",
        "Online",
        None,
        date(2010, 1, 1),
        "Online",
    )
    assert candidate["fact_ventas"][0][3] is None
    assert candidate["fact_ventas"][0][-2:] == (Decimal("1234.50"), Decimal("1.00"))
    assert valid_rows == before


def test_calendar_covers_rates_orders_deliveries_not_birthdays(valid_rows):
    valid_rows["Exchange_Rates.csv"].append(
        dict(
            numero_registro_origen=2, Date="12/30/2019", Currency="EUR", Exchange="0.9"
        )
    )
    candidate = transform.transform_records(valid_rows)
    assert candidate["dim_fecha"] == [
        (date(2019, 12, 30),),
        (date(2019, 12, 31),),
        (date(2020, 1, 1),),
        (date(2020, 1, 2),),
        (date(2020, 1, 3),),
    ]


def test_rate_does_not_convert_usd_reference_price(valid_rows):
    valid_rows["Sales.csv"][0]["Currency Code"] = "EUR"
    valid_rows["Exchange_Rates.csv"][0].update(Currency="EUR", Exchange="0.85")
    candidate = transform.transform_records(valid_rows)
    assert candidate["tipos_cambio"][0][-1] == Decimal("0.85")
    assert candidate["fact_ventas"][0][-2:] == (Decimal("2.00"), Decimal("1.00"))


def test_invalid_money_cannot_reach_candidate(valid_rows):
    valid_rows["Products.csv"][0]["Unit Price USD"] = "bad"
    with pytest.raises(ValueError):
        transform.transform_records(valid_rows)
