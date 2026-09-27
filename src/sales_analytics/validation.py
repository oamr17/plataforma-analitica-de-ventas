"""Reglas de registros y conciliación, sin escrituras ni publicación."""

from collections import Counter
from collections.abc import Mapping
from functools import partial

from sales_analytics.converters import parse_date, parse_decimal, parse_integer
from sales_analytics.validation_changes import check_changes
from sales_analytics.validation_checks import (
    check_balances,
    check_contract,
    check_groups,
    check_links,
    check_warnings,
)

# Alcance revisado del snapshot inicial; ampliar exige revisar el contrato de origen.
INITIAL_CURRENCIES = frozenset({"AUD", "CAD", "EUR", "GBP", "USD"})
INITIAL_PHYSICAL_STORES = frozenset(range(1, 67))

KEYS = {
    "Customers.csv": ("CustomerKey",),
    "Products.csv": ("ProductKey",),
    "Stores.csv": ("StoreKey",),
    "Sales.csv": ("Order Number", "Line Item"),
    "Exchange_Rates.csv": ("Date", "Currency"),
}
DESCRIPTIVE = {
    "Customers.csv": (
        "Gender",
        "Name",
        "City",
        "State Code",
        "State",
        "Zip Code",
        "Country",
        "Continent",
    ),
    "Products.csv": (
        "Product Name",
        "Brand",
        "Color",
        "SubcategoryKey",
        "Subcategory",
        "CategoryKey",
        "Category",
    ),
    "Stores.csv": ("Country", "State"),
}


def add_issue(
    issues: list,
    file: str | None,
    ordinal: int | None,
    rule: str,
    reason: str,
    **evidence,
) -> None:
    issues.append(
        {
            "archivo": file,
            "numero_registro_origen": ordinal,
            "regla": rule,
            "clasificacion": {"C": "error_critico", "R": "rechazo", "W": "advertencia"}[
                rule[0]
            ],
            "motivo": reason,
            "evidencia": evidence,
        }
    )


def _field_specs(file: str, fields) -> dict:
    specs = {}
    for field in ("CustomerKey", "ProductKey", "StoreKey", "Order Number", "Line Item"):
        if field in fields:
            specs[field] = (
                partial(parse_integer, bits=64 if field == "Order Number" else 32),
                "R1",
                False,
                0 if field == "StoreKey" else 1,
            )
    for field in ("Birthday", "Open Date", "Order Date", "Delivery Date", "Date"):
        if field in fields:
            specs[field] = (parse_date, "R2", field == "Delivery Date", None)
    if file == "Sales.csv":
        specs["Quantity"] = (parse_integer, "R3", False, 1)
    if file == "Products.csv":
        for field in ("Unit Price USD", "Unit Cost USD"):
            specs[field] = (partial(parse_decimal, scale=2, money=True), "R4", False, 0)
    if file == "Exchange_Rates.csv":
        specs["Exchange"] = (partial(parse_decimal, scale=8), "R6", False, None)
    if file == "Stores.csv":
        specs["Square Meters"] = (parse_integer, "R7", True, 1)
    return specs


def _convert_row(file: str, raw: dict, specs: dict, issues: list) -> dict:
    row = dict(raw)
    for field, (parser, rule, optional, minimum) in specs.items():
        try:
            value = raw[field]
            parsed = None if optional and not value.strip() else parser(value)
            if parsed is not None and minimum is not None and parsed < minimum:
                raise ValueError("Fuera del dominio.")
            if field == "Exchange" and parsed <= 0:
                raise ValueError("Tasa no positiva.")
            row[field] = parsed
        except ValueError:
            row[field] = None
            add_issue(
                issues,
                file,
                row["numero_registro_origen"],
                rule,
                "Campo obligatorio, formato o dominio incompatible.",
                campo=field,
            )
    for field in ("Currency", "Currency Code"):
        if field in row and not row[field].strip():
            add_issue(
                issues,
                file,
                row["numero_registro_origen"],
                "R6",
                "Moneda vacía.",
                campo=field,
            )
    return row


def convert_records(records: Mapping[str, list], issues: list) -> dict:
    converted = {}
    for file, rows in records.items():
        specs = _field_specs(file, rows[0]) if rows else {}
        converted[file] = [_convert_row(file, row, specs, issues) for row in rows]
    return converted


def summarize(records: Mapping[str, list], issues: list, indirect: set) -> dict:
    rejected = {
        (i["archivo"], i["numero_registro_origen"])
        for i in issues
        if i["regla"].startswith("R")
    }
    counts = {
        file: {
            "entradas": len(rows),
            "rechazados": sum(
                (file, r["numero_registro_origen"]) in rejected for r in rows
            ),
        }
        for file, rows in records.items()
    }
    for value in counts.values():
        value["aceptados"] = value["entradas"] - value["rechazados"]
    critical = sum(i["clasificacion"] == "error_critico" for i in issues)
    total_rejected = sum(c["rechazados"] for c in counts.values())
    return {
        "entradas": sum(c["entradas"] for c in counts.values()),
        "aceptados": sum(c["aceptados"] for c in counts.values()),
        "rechazados": total_rejected,
        "criticos": critical,
        "advertencias": sum(i["clasificacion"] == "advertencia" for i in issues),
        "por_regla": dict(Counter(i["regla"] for i in issues)),
        "lineas_afectadas_indirectamente": len(indirect),
        "lineas_afectadas_sin_rechazo_directo": len(
            indirect - {o for f, o in rejected if f == "Sales.csv"}
        ),
        "por_archivo": counts,
        "apto": critical == 0 and total_rejected == 0,
        "incidencias": issues,
    }


def validate_records(
    records: Mapping[str, list],
    *,
    previous: Mapping[str, list] | None = None,
    authorizations: Mapping[str, str] | None = None,
    reviewed_currencies: set[str] | frozenset[str] = INITIAL_CURRENCIES,
    reviewed_physical_stores: set[int] | frozenset[int] = INITIAL_PHYSICAL_STORES,
) -> dict:
    """Aceptados significa sin rechazo propio; los críticos bloquean todo el lote."""
    issues = []
    add = partial(add_issue, issues)
    typed = convert_records(records, issues)
    check_contract(typed, reviewed_currencies, reviewed_physical_stores, add)
    indexes = check_groups(typed, KEYS, add)
    indirect = check_links(typed, indexes, issues, add)
    balance = check_balances(typed, indexes, add)
    invalid_fields = {
        (i["archivo"], i["numero_registro_origen"], i["evidencia"].get("campo"))
        for i in issues
        if i["regla"].startswith("R")
    }
    check_warnings(typed, DESCRIPTIVE, invalid_fields, reviewed_physical_stores, add)
    prior = convert_records(previous, []) if previous is not None else None
    applied = check_changes(typed, prior, KEYS, DESCRIPTIVE, authorizations or {}, add)
    result = summarize(typed, issues, indirect)
    result["conciliacion_totales"] = balance
    result["autorizaciones_aplicadas"] = applied
    result["referencia_previa"] = previous is not None
    result["contrato_revisado"] = {
        "monedas": sorted(reviewed_currencies),
        "sucursales_fisicas": sorted(reviewed_physical_stores),
    }
    return result
