"""Controles de publicación; no son consultas de indicadores para la aplicación."""

import hashlib
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

from psycopg import sql

from sales_analytics.extract import SOURCES, iter_records, read_snapshot
from sales_analytics.transform import COLUMNS
from sales_analytics.validation import convert_records


class LoadBlocked(ValueError):
    """Control de aceptación fallido, sin datos personales en el motivo."""

    def __init__(self, rule: str, reason: str):
        super().__init__(reason)
        self.rule = rule


def verify_source_evidence(attempt: dict, records: dict) -> None:
    for name, (_, headers, encoding) in SOURCES.items():
        detail = attempt["archivos"][name]
        raw, _ = read_snapshot(Path(detail["ruta"]))
        if hashlib.sha256(raw).hexdigest() != detail["sha256"]:
            raise LoadBlocked("C1", "El origen cambió después de la extracción.")
        if name in records:
            original = [
                dict(zip(headers, values, strict=True), numero_registro_origen=n)
                for n, values in iter_records(raw, headers, encoding)
            ]
            if original != records[name]:
                raise LoadBlocked(
                    "C4", "Staging no coincide con los registros de origen."
                )


def same_snapshot(left: dict, right: dict) -> bool:
    return (
        left["modo"] == right["modo"]
        and left["version_reglas"] == right["version_reglas"]
        and {n: d["sha256"] for n, d in left["archivos"].items()}
        == {n: d["sha256"] for n, d in right["archivos"].items()}
        and left["conteos"]["validacion"]["version_validacion"]
        == right["conteos"]["validacion"]["version_validacion"]
        and left["conteos"]["validacion"]["contrato_revisado"]
        == right["conteos"]["validacion"]["contrato_revisado"]
    )


def source_controls(records: dict) -> tuple:
    """Control independiente: agrupa unidades por producto desde staging tipado."""
    typed = convert_records(records, [])
    units = Counter()
    orders = set()
    for row in typed["Sales.csv"]:
        units[row["ProductKey"]] += row["Quantity"]
        orders.add(row["Order Number"])
    with localcontext() as ctx:
        ctx.prec = 80
        revenue = sum(
            (
                units[p["ProductKey"]] * p["Unit Price USD"]
                for p in typed["Products.csv"]
            ),
            Decimal(0),
        )
        cost = sum(
            (
                units[p["ProductKey"]] * p["Unit Cost USD"]
                for p in typed["Products.csv"]
            ),
            Decimal(0),
        )
    return len(typed["Sales.csv"]), len(orders), sum(units.values()), revenue, cost


def reconcile(conn, candidate: dict, expected: tuple) -> dict:
    # Los totales solos no detectan claves o atributos intercambiados.
    for table, columns in COLUMNS.items():
        if table == "fact_ventas":
            continue
        actual = conn.execute(
            sql.SQL("SELECT {} FROM dw.{}").format(
                sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table)
            )
        ).fetchall()
        if Counter(actual) != Counter(candidate[table]):
            raise LoadBlocked(
                "C4", "Dimensiones, fechas o tasas no concilian con el candidato."
            )
    actual = conn.execute(
        "SELECT f.numero_pedido,f.linea_pedido,f.fecha_pedido,f.fecha_entrega,"
        "c.customer_key,p.product_key,s.store_key,f.cantidad,f.moneda_pedido,"
        "f.precio_referencia_usd,f.costo_referencia_usd "
        "FROM dw.fact_ventas f JOIN dw.dim_cliente c USING(cliente_id) "
        "JOIN dw.dim_producto p USING(producto_id) "
        "JOIN dw.dim_sucursal s USING(sucursal_id) "
        "JOIN dw.dim_fecha d ON d.fecha=f.fecha_pedido "
        "JOIN dw.tipos_cambio t ON t.fecha=f.fecha_pedido AND t.moneda=f.moneda_pedido"
    ).fetchall()
    totals = conn.execute(
        "SELECT count(*),count(DISTINCT numero_pedido),coalesce(sum(cantidad),0),"
        "coalesce(sum(cantidad*precio_referencia_usd),0),"
        "coalesce(sum(cantidad*costo_referencia_usd),0) FROM dw.fact_ventas"
    ).fetchone()
    if Counter(actual) != Counter(candidate["fact_ventas"]) or totals != expected:
        raise LoadBlocked(
            "C4", "Hechos, relaciones o importes no concilian con el origen."
        )
    return dict(
        zip(
            (
                "lineas",
                "pedidos",
                "unidades",
                "ingresos_control_usd",
                "costos_control_usd",
            ),
            (totals[0], totals[1], totals[2], str(totals[3]), str(totals[4])),
            strict=True,
        )
    )
