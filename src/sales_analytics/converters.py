"""Conversores del contrato CSV. No corrigen ni modifican registros de origen."""

import re
from datetime import date
from decimal import Decimal


def parse_integer(value: str, *, bits: int = 32) -> int:
    text = value.strip()
    if not re.fullmatch(r"-?[0-9]+", text):
        raise ValueError("Entero no interpretable.")
    result = int(text)
    if not -(2 ** (bits - 1)) <= result < 2 ** (bits - 1):
        raise ValueError("Entero fuera del tipo de destino.")
    return result


def parse_date(value: str) -> date:
    text = value.strip()
    if not re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}", text):
        raise ValueError("Fecha fuera del formato M/d/yyyy.")
    month, day, year = map(int, text.split("/"))
    return date(year, month, day)


def parse_decimal(value: str, *, scale: int, money: bool = False) -> Decimal:
    text = value.strip()
    if money:
        pattern = r"-?\$?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?"
    else:
        pattern = r"-?[0-9]+(?:\.[0-9]+)?"
    if not re.fullmatch(pattern, text):
        raise ValueError("Decimal no interpretable.")
    result = Decimal(text.replace("$", "").replace(",", ""))
    if result.as_tuple().exponent < -scale or abs(result) >= Decimal(10) ** (
        18 - scale
    ):
        raise ValueError("Decimal fuera de la precisión de destino; no se redondea.")
    return result
