"""Controles de cardinalidad, relaciones, pedidos y advertencias del lote."""

import calendar
from collections import defaultdict
from datetime import timedelta


def check_groups(data: dict, keys: dict, add) -> dict:
    indexes = {}
    for file, rows in data.items():
        index = defaultdict(list)
        for row in rows:
            key = tuple(row[field] for field in keys[file])
            if all(value is not None and value != "" for value in key):
                index[key].append(row)
        indexes[file] = index
        for group in index.values():
            if len(group) > 1:
                for row in group:
                    add(
                        file,
                        row["numero_registro_origen"],
                        "C2",
                        "Clave natural duplicada.",
                        registros=[r["numero_registro_origen"] for r in group],
                    )
    orders = defaultdict(list)
    for row in data["Sales.csv"]:
        if row["Order Number"] is not None:
            orders[row["Order Number"]].append(row)
    header = ("CustomerKey", "Order Date", "Currency Code", "StoreKey", "Delivery Date")
    for group in orders.values():
        if len({tuple(r[f] for f in header) for r in group}) > 1:
            add(
                "Sales.csv",
                group[0]["numero_registro_origen"],
                "C3",
                "Encabezado incompatible entre líneas del pedido.",
                registros=[r["numero_registro_origen"] for r in group],
            )
    for key, fields in [
        ("CategoryKey", ("Category",)),
        ("SubcategoryKey", ("Subcategory", "CategoryKey")),
    ]:
        groups = defaultdict(list)
        for row in data["Products.csv"]:
            if row[key].strip():
                groups[row[key]].append(row)
        for group in groups.values():
            if any(len({r[f] for r in group if r[f].strip()}) > 1 for f in fields):
                add(
                    "Products.csv",
                    group[0]["numero_registro_origen"],
                    "C7",
                    "Clasificación de catálogo ambigua.",
                    campo=key,
                    registros=[r["numero_registro_origen"] for r in group],
                )
    return indexes


def check_links(data: dict, indexes: dict, issues: list, add) -> set:
    rejected = {
        (i["archivo"], i["numero_registro_origen"])
        for i in issues
        if i["regla"].startswith("R")
    }
    indirect = set()
    for rate in data["Exchange_Rates.csv"]:
        if (
            rate["Currency"] == "USD"
            and rate["Exchange"] is not None
            and rate["Exchange"] != 1
        ):
            add(
                "Exchange_Rates.csv",
                rate["numero_registro_origen"],
                "C8",
                "La tasa USD debe ser 1.",
            )
    for row in data["Sales.csv"]:
        ordinal = row["numero_registro_origen"]
        links = {}
        for file, field in [
            ("Customers.csv", "CustomerKey"),
            ("Products.csv", "ProductKey"),
            ("Stores.csv", "StoreKey"),
        ]:
            value = row[field]
            matches = indexes[file].get((value,), []) if value is not None else []
            links[file] = matches[0] if len(matches) == 1 else None
            if value is not None and not matches:
                add(
                    "Sales.csv",
                    ordinal,
                    "R5",
                    "Referencia ausente del catálogo.",
                    campo=field,
                )
            if len(matches) > 1:
                add(
                    "Sales.csv",
                    ordinal,
                    "C4",
                    "La unión multiplica la línea.",
                    destino=file,
                )
            if any((file, r["numero_registro_origen"]) in rejected for r in matches):
                indirect.add(ordinal)
        if row["Order Date"] is not None and row["Currency Code"].strip():
            rates = indexes["Exchange_Rates.csv"].get(
                (row["Order Date"], row["Currency Code"]), []
            )
            if not rates:
                add("Sales.csv", ordinal, "R6", "No existe tasa para fecha y moneda.")
            if len(rates) > 1:
                add(
                    "Sales.csv",
                    ordinal,
                    "C4",
                    "La unión con tasas multiplica la línea.",
                )
            if any(
                ("Exchange_Rates.csv", r["numero_registro_origen"]) in rejected
                for r in rates
            ):
                indirect.add(ordinal)
        order = row["Order Date"]
        delivery = row["Delivery Date"]
        if order is not None:
            if delivery is not None and delivery < order:
                add("Sales.csv", ordinal, "R2", "Entrega anterior al pedido.")
            for file, field in [
                ("Customers.csv", "Birthday"),
                ("Stores.csv", "Open Date"),
            ]:
                linked = links[file]
                if linked and linked[field] is not None and order < linked[field]:
                    add(
                        "Sales.csv",
                        ordinal,
                        "R2",
                        "Pedido anterior a la fecha de referencia.",
                        referencia=file,
                    )
            customer = links["Customers.csv"]
            if (
                customer
                and customer["Birthday"] is not None
                and customer["Birthday"] <= order
            ):
                birth = customer["Birthday"]
                age = (
                    order.year
                    - birth.year
                    - ((order.month, order.day) < (birth.month, birth.day))
                )
                if age < 18:
                    add(
                        "Sales.csv",
                        ordinal,
                        "W6",
                        "Edad menor de 18 al comprar; no se rechaza.",
                    )
    return indirect


def check_contract(data: dict, currencies, physical_stores, add) -> None:
    for file, field in [
        ("Sales.csv", "Currency Code"),
        ("Exchange_Rates.csv", "Currency"),
    ]:
        for row in data[file]:
            if row[field].strip() and row[field] not in currencies:
                add(
                    file,
                    row["numero_registro_origen"],
                    "C8",
                    "Moneda pendiente de revisión del contrato; no es un rechazo.",
                    campo=field,
                )
    for row in data["Stores.csv"]:
        if (
            row["StoreKey"] is not None
            and row["StoreKey"] != 0
            and row["StoreKey"] not in physical_stores
        ):
            add(
                "Stores.csv",
                row["numero_registro_origen"],
                "C8",
                "Canal de sucursal nueva pendiente de revisión del contrato.",
            )


def check_warnings(
    data: dict, descriptive: dict, invalid_fields: set, physical_stores, add
) -> None:
    _catalog_warnings(data, descriptive, add)
    _value_warnings(data, invalid_fields, add)
    _order_warnings(data, invalid_fields, physical_stores, add)
    _coverage_warnings(data, add)


def _catalog_warnings(data: dict, descriptive: dict, add) -> None:
    for file, fields in descriptive.items():
        for row in data[file]:
            missing = [f for f in fields if not row[f].strip()]
            if missing:
                add(
                    file,
                    row["numero_registro_origen"],
                    "W7",
                    "Atributos descriptivos vacíos.",
                    campos=missing,
                )
    for file, field in [
        ("Customers.csv", "CustomerKey"),
        ("Products.csv", "ProductKey"),
        ("Stores.csv", "StoreKey"),
    ]:
        used = {r[field] for r in data["Sales.csv"] if r[field] is not None}
        for row in data[file]:
            if row[field] is not None and row[field] not in used:
                add(
                    file,
                    row["numero_registro_origen"],
                    "W4",
                    "Entrada del catálogo sin ventas observadas.",
                )
    names = defaultdict(list)
    for row in data["Customers.csv"]:
        if row["Name"].strip():
            names[row["Name"]].append(row)
    for group in names.values():
        if len(group) > 1:
            for row in group:
                add(
                    "Customers.csv",
                    row["numero_registro_origen"],
                    "W4",
                    "Nombre repetido; conservar identidades.",
                )


def _value_warnings(data: dict, invalid_fields: set, add) -> None:
    for row in data["Stores.csv"]:
        if (
            row["StoreKey"] == 0
            and row["Square Meters"] is None
            and ("Stores.csv", row["numero_registro_origen"], "Square Meters")
            not in invalid_fields
        ):
            add(
                "Stores.csv",
                row["numero_registro_origen"],
                "W1",
                "Superficie Online no informada.",
            )
    for row in data["Products.csv"]:
        price, cost = row["Unit Price USD"], row["Unit Cost USD"]
        if (
            price is not None
            and cost is not None
            and (price == 0 or cost == 0 or cost > price)
        ):
            add(
                "Products.csv",
                row["numero_registro_origen"],
                "W6",
                "Precio/costo cero o costo superior al precio.",
            )


def _order_warnings(data: dict, invalid_fields: set, physical_stores, add) -> None:
    orders = defaultdict(list)
    for row in data["Sales.csv"]:
        store = row["StoreKey"]
        if (store == 0 or store in physical_stores) and (
            "Sales.csv",
            row["numero_registro_origen"],
            "Delivery Date",
        ) not in invalid_fields:
            empty = row["Delivery Date"] is None
            if empty and store != 0:
                add(
                    "Sales.csv",
                    row["numero_registro_origen"],
                    "W1",
                    "Entrega física no informada.",
                )
            elif (empty and store == 0) or (not empty and store != 0):
                add(
                    "Sales.csv",
                    row["numero_registro_origen"],
                    "W2",
                    "Patrón de entrega distinto del snapshot inicial.",
                )
        if row["Order Number"] is not None and row["Line Item"] is not None:
            orders[row["Order Number"]].append(row)
    for group in orders.values():
        lines = sorted({r["Line Item"] for r in group})
        if any(value != i for i, value in enumerate(lines, 1)):
            add(
                "Sales.csv",
                group[0]["numero_registro_origen"],
                "W3",
                "Numeración no consecutiva.",
                registros=[r["numero_registro_origen"] for r in group],
            )
        products = [r["ProductKey"] for r in group if r["ProductKey"] is not None]
        if len(set(products)) < len(products):
            add(
                "Sales.csv",
                group[0]["numero_registro_origen"],
                "W3",
                "Producto repetido en el pedido.",
                registros=[r["numero_registro_origen"] for r in group],
            )


def _coverage_warnings(data: dict, add) -> None:
    dates = sorted(
        {r["Order Date"] for r in data["Sales.csv"] if r["Order Date"] is not None}
    )
    for before, after in zip(dates, dates[1:], strict=False):
        days = (after - before).days - 1
        if days:
            add(
                "Sales.csv",
                None,
                "W5",
                "Intervalo sin registros; no se imputan ventas.",
                inicio=str(before + timedelta(days=1)),
                fin=str(after - timedelta(days=1)),
                dias=days,
            )
    if dates:
        for boundary, incomplete in [
            (dates[0], dates[0].day != 1),
            (
                dates[-1],
                dates[-1].day
                != calendar.monthrange(dates[-1].year, dates[-1].month)[1],
            ),
        ]:
            if incomplete:
                add(
                    "Sales.csv",
                    None,
                    "W5",
                    "Mes de borde incompleto.",
                    fecha_corte=str(boundary),
                )


def check_conservation(expected: tuple, actual: tuple, add) -> bool:
    """Controles internos: líneas, unidades, valoración y costo en centavos exactos."""
    if expected != actual:
        add(
            "Sales.csv",
            None,
            "C4",
            "No concilian líneas, unidades o importes de control.",
        )
        return False
    return True


def check_balances(data: dict, indexes: dict, add) -> str:
    """Contrasta sumas por producto con recorrido por línea; no produce KPIs."""
    quantities = defaultdict(int)
    actual = [0, 0, 0, 0]
    for row in data["Sales.csv"]:
        references = [
            ("Customers.csv", (row["CustomerKey"],)),
            ("Products.csv", (row["ProductKey"],)),
            ("Stores.csv", (row["StoreKey"],)),
            ("Exchange_Rates.csv", (row["Order Date"], row["Currency Code"])),
        ]
        if row["Quantity"] is None or any(
            len(indexes[f].get(k, [])) != 1 for f, k in references
        ):
            return "no_evaluable_por_defectos"
        product = indexes["Products.csv"][(row["ProductKey"],)][0]
        price, cost = product["Unit Price USD"], product["Unit Cost USD"]
        if price is None or cost is None:
            return "no_evaluable_por_defectos"
        quantity = row["Quantity"]
        quantities[row["ProductKey"]] += quantity
        actual[0] += 1
        actual[1] += quantity
        # La precisión del catálogo hace exacta la conversión a centavos enteros.
        actual[2] += quantity * int(price * 100)
        actual[3] += quantity * int(cost * 100)
    amounts = []
    for field in ("Unit Price USD", "Unit Cost USD"):
        amounts.append(
            sum(
                q * int(indexes["Products.csv"][(key,)][0][field] * 100)
                for key, q in quantities.items()
            )
        )
    expected = (len(data["Sales.csv"]), sum(quantities.values()), *amounts)
    return (
        "conforme"
        if check_conservation(expected, tuple(actual), add)
        else "no_conforme"
    )
