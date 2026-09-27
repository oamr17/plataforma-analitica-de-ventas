"""Contrato K1–K10 con datos didácticos aislados y transacción reversible."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from sales_analytics import analytics
from sales_analytics.db import connect_database


@pytest.fixture
def example(writer, run_id):
    writer.execute(
        "UPDATE ops.ejecuciones SET estado='publicado',publication_id=1,"
        "fecha_pedido_min='2020-01-01',fecha_pedido_max='2020-02-29' WHERE run_id=%s",
        (run_id,),
    )
    writer.execute(
        "INSERT INTO dw.dim_fecha(fecha) "
        "SELECT generate_series('2020-01-01'::date,'2021-02-28','1 day')::date"
    )
    customers = dict(
        writer.execute(
            "INSERT INTO dw.dim_cliente(customer_key,pais,fecha_nacimiento) "
            "VALUES (1,'España','1980-01-01'),(2,'Canadá','1980-01-01'),"
            "(3,'España','1980-01-01') RETURNING customer_key,cliente_id"
        ).fetchall()
    )
    products = dict(
        writer.execute(
            "INSERT INTO dw.dim_producto(product_key,nombre,categoria_codigo,"
            "precio_catalogo_usd,costo_catalogo_usd) "
            "VALUES (1,'X','01',999,999),(2,'Y','02',999,999),"
            "(3,'Sin ventas','03',1,1) RETURNING product_key,producto_id"
        ).fetchall()
    )
    stores = dict(
        writer.execute(
            "INSERT INTO dw.dim_sucursal(store_key,canal,pais_origen,fecha_apertura) "
            "VALUES (0,'Online','Online','2000-01-01'),"
            "(1,'Físico','España','2000-01-01'),(2,'Físico','Canadá','2000-01-01') "
            "RETURNING store_key,sucursal_id"
        ).fetchall()
    )
    writer.execute(
        "INSERT INTO dw.tipos_cambio(fecha,moneda,tasa_original) "
        "SELECT fecha,'EUR',0.8 FROM dw.dim_fecha"
    )
    rows = [
        (1, 1, "2020-01-10", 1, 1, 1, 2, 100, 60),
        (2, 1, "2020-01-20", 1, 1, 0, 1, 100, 60),
        (2, 2, "2020-01-20", 1, 2, 0, 1, 50, 20),
        (3, 1, "2020-01-25", 2, 2, 1, 3, 50, 20),
        (4, 1, "2020-02-10", 1, 1, 1, 2, 100, 60),
    ]
    with writer.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO dw.fact_ventas(numero_pedido,linea_pedido,fecha_pedido,"
            "cliente_id,producto_id,sucursal_id,cantidad,precio_referencia_usd,"
            "costo_referencia_usd,moneda_pedido,run_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'EUR',%s)",
            [
                (
                    o,
                    line,
                    d,
                    customers[c],
                    products[p],
                    stores[s],
                    q,
                    price,
                    cost,
                    run_id,
                )
                for o, line, d, c, p, s, q, price, cost in rows
            ],
        )
    return writer


def read(conn, start="2020-01-01", end="2020-01-31", **kwargs):
    return analytics.read_analytics(
        conn, date.fromisoformat(start), date.fromisoformat(end), **kwargs
    )


def test_full_example_and_equivalent_routes(example):
    complete = read(example, "2020-01-01", "2020-02-29")["resumen"][0]
    assert tuple(
        complete[k]
        for k in (
            "lineas",
            "pedidos",
            "unidades",
            "ingresos_usd",
            "costos_usd",
            "margen_usd",
        )
    ) == (5, 4, 9, Decimal("700"), Decimal("380"), Decimal("320"))
    result = read(example)
    total = result["resumen"][0]
    assert (total["lineas"], total["pedidos"], total["unidades"]) == (4, 3, 7)
    assert total["ingresos_usd"] == Decimal("500")
    assert total["costos_usd"] == Decimal("260")
    assert total["margen_usd"] == Decimal("240")
    assert total["margen_pct"] == Decimal("48")
    assert total["ticket_usd"].quantize(Decimal(".01")) == Decimal("166.67")
    assert total["tasa_recurrencia_pct"] == Decimal("50")
    for group in ("producto", "sucursal", "canal"):
        assert sum(r["ingresos_usd"] for r in result[group]) == total["ingresos_usd"]
    assert result["metadatos"]["publication_id"] == 1
    assert example.execute(
        "SELECT count(*),sum(ingresos_usd) FROM marts.pedidos "
        "WHERE fecha_pedido < '2020-02-01'"
    ).fetchone() == (3, Decimal("500"))
    assert example.execute("SELECT count(*) FROM marts.ventas_base").fetchone() == (5,)
    assert example.execute(
        "SELECT count(*) FROM marts.clientes_primera_compra"
    ).fetchone() == (2,)


@pytest.mark.parametrize(
    "start,end,filters,expected",
    [
        ("2020-01-01", "2020-01-31", {}, (3, 2, 2, 1)),
        ("2020-01-01", "2020-01-31", {"product_keys": [1]}, (2, 1, 1, 1)),
        ("2020-01-01", "2020-01-31", {"product_keys": [2]}, (2, 2, 2, 0)),
        ("2020-01-01", "2020-01-31", {"canales": ["Online"]}, (1, 1, 1, 0)),
        ("2020-01-20", "2020-01-31", {}, (2, 2, 1, 0)),
        ("2020-02-01", "2020-02-29", {}, (1, 1, 0, 0)),
    ],
)
def test_approved_filter_examples(example, start, end, filters, expected):
    row = read(example, start, end, filters=filters)["resumen"][0]
    assert (
        tuple(row[k] for k in ("pedidos", "clientes", "nuevos", "recurrentes"))
        == expected
    )


def test_distinct_counts_are_not_additive_and_product_does_not_restore_basket(example):
    result = read(example)
    assert sum(r["pedidos"] for r in result["producto"]) == 4
    assert sum(r["clientes"] for r in result["producto"]) == 3
    selected = read(example, filters={"product_keys": [1]})["resumen"][0]
    assert selected["ingresos_usd"] == Decimal("300")
    assert selected["ticket_usd"] == Decimal("150")
    assert len(result["producto"]) == 2
    stores = {r["store_key"]: r for r in result["sucursal"]}
    assert stores[0]["ingresos_usd"] == Decimal("150")
    assert stores[1]["ingresos_usd"] == Decimal("350")
    assert stores[2]["sin_registros"] and stores[2]["ingresos_usd"] == 0


def test_empty_selection_and_zero_denominators(example):
    row = read(example, filters={"product_keys": []})["resumen"][0]
    for field in (
        "lineas",
        "pedidos",
        "unidades",
        "ingresos_usd",
        "costos_usd",
        "margen_usd",
        "clientes",
        "nuevos",
        "recurrentes",
    ):
        assert row[field] == 0
    assert row["ticket_usd"] is None and row["margen_pct"] is None
    assert row["tasa_recurrencia_pct"] is None
    example.execute("UPDATE dw.fact_ventas SET precio_referencia_usd=0")
    row = read(example)["resumen"][0]
    assert row["margen_usd"] == Decimal("-260") and row["margen_pct"] is None


def test_closed_month_growth_and_partial_selection(example):
    rows = read(example, "2020-01-01", "2020-02-29")["mes"]
    assert rows[0]["crecimiento_pct"] is None
    assert rows[1]["crecimiento_pct"] == Decimal("-60")
    assert rows[1]["ingresos_anterior_usd"] == Decimal("500")
    for start, end in (
        ("2020-02-10", "2020-02-29"),
        ("2020-02-01", "2020-02-20"),
        ("2020-01-20", "2020-02-29"),
    ):
        assert read(example, start, end)["mes"][-1]["crecimiento_pct"] is None


def test_previous_zero_month_is_not_skipped(example):
    example.execute("UPDATE ops.ejecuciones SET fecha_pedido_max='2020-04-30'")
    rows = read(example, "2020-04-01", "2020-04-30")["mes"]
    assert rows[0]["ingresos_anterior_usd"] == 0
    assert rows[0]["crecimiento_pct"] is None


def test_truncated_february_2021_and_gap_warnings(example, run_id):
    example.execute("UPDATE ops.ejecuciones SET fecha_pedido_max='2021-02-20'")
    assert (
        read(example, "2021-02-01", "2021-02-28")["mes"][0]["crecimiento_pct"] is None
    )
    example.execute(
        "INSERT INTO ops.incidencias(run_id,regla,clasificacion,motivo,evidencia) "
        "VALUES (%s,'W5','advertencia','Intervalo sin registros',%s)",
        (run_id, '{"inicio":"2020-01-15","fin":"2020-01-16","dias":2}'),
    )
    row = read(example, "2020-02-01", "2020-02-29")["mes"][0]
    assert row["huecos_conocidos"] and row["crecimiento_pct"] is None
    result = read(example, "2020-02-01", "2020-02-29", accept_coverage_warnings=True)
    assert result["mes"][0]["crecimiento_pct"] == Decimal("-60")
    assert result["metadatos"]["advertencias_cobertura"]


def test_shared_connection_never_commits_or_closes(example):
    pid = example.info.backend_pid
    transaction = example.execute("SELECT txid_current()").fetchone()[0]
    read(example)
    assert not example.closed and example.info.backend_pid == pid
    assert example.execute("SELECT txid_current()").fetchone()[0] == transaction
    example.rollback()
    assert example.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (0,)


def test_partial_month_uses_same_lines_and_new_customers_as_total(example):
    result = read(example, "2020-01-20", "2020-01-31")
    for field in (
        "lineas",
        "pedidos",
        "clientes",
        "nuevos",
        "recurrentes",
        "ingresos_usd",
    ):
        assert result["mes"][0][field] == result["resumen"][0][field]


@pytest.mark.parametrize(
    "filters,revenue",
    [
        ({"categoria_codigos": ["01"]}, 300),
        ({"customer_keys": [2]}, 150),
        ({"paises_cliente": ["Canadá"]}, 150),
        ({"store_keys": [0]}, 150),
        ({"paises_sucursal": ["España"]}, 350),
        ({"paises_sucursal": ["Online"]}, 0),
        ({"monedas": ["EUR"]}, 500),
        ({"monedas": ["USD"]}, 0),
        ({"paises_cliente": ["' OR true --"]}, 0),
    ],
)
def test_all_filters_use_parameters_and_distinguish_geography(
    example, filters, revenue
):
    assert read(example, filters=filters)["resumen"][0]["ingresos_usd"] == Decimal(
        revenue
    )


def test_global_first_purchase_survives_product_and_channel_filter(example):
    result = read(
        example,
        "2020-01-20",
        "2020-01-31",
        filters={"product_keys": [2], "canales": ["Online"]},
    )
    row = result["resumen"][0]
    assert row["clientes"] == 1 and row["nuevos"] == 0 and row["recurrentes"] == 0


def test_mom_same_filters_zero_previous_and_negative_margin(example):
    assert read(example, "2020-02-01", "2020-02-29", filters={"product_keys": [1]})[
        "mes"
    ][0]["crecimiento_pct"].quantize(Decimal(".01")) == Decimal("-33.33")
    example.execute(
        "UPDATE dw.fact_ventas SET precio_referencia_usd=0 "
        "WHERE fecha_pedido<'2020-02-01'"
    )
    assert (
        read(example, "2020-02-01", "2020-02-29")["mes"][0]["crecimiento_pct"] is None
    )
    example.execute("UPDATE dw.fact_ventas SET costo_referencia_usd=200")
    row = read(example, "2020-02-01", "2020-02-29")["resumen"][0]
    assert row["margen_usd"] == Decimal("-200") and row["margen_pct"] == Decimal("-100")


def test_reader_reads_views_but_cannot_modify_them(urls):
    with connect_database("reader", environ=urls) as conn:
        assert read(conn)["resumen"][0]["ingresos_usd"] == 0
        for view in ("ventas_base", "pedidos", "clientes_primera_compra", "sucursales"):
            assert conn.execute(
                sql.SQL("SELECT count(*) FROM marts.{}").format(sql.Identifier(view))
            ).fetchone() == (0,)
    with psycopg.connect(urls["SALES_READER_DATABASE_URL"]) as conn:
        for statement in (
            "DELETE FROM marts.sucursales",
            "UPDATE marts.sucursales SET canal='Online'",
            "INSERT INTO marts.sucursales(store_key,canal) VALUES(99,'Online')",
            "DROP VIEW marts.sucursales",
            "SELECT * FROM dw.dim_sucursal",
        ):
            with (
                pytest.raises(psycopg.errors.InsufficientPrivilege),
                conn.transaction(),
            ):
                conn.execute(statement)


@pytest.mark.parametrize(
    "filters",
    [
        {"unknown": [1]},
        {"store_keys": "0"},
        {"product_keys": [True]},
        {"categoria_codigos": [1]},
    ],
)
def test_invalid_filter_is_rejected_without_widening_selection(example, filters):
    with pytest.raises(ValueError):
        read(example, filters=filters)


def test_invalid_period_is_rejected(example):
    with pytest.raises(ValueError):
        read(example, "2020-02-01", "2020-01-01")


def test_product_ranking_ties_use_natural_key(example):
    example.execute(
        "UPDATE dw.fact_ventas SET precio_referencia_usd=100 WHERE producto_id IN "
        "(SELECT producto_id FROM dw.dim_producto WHERE product_key=2)"
    )
    result = read(example, filters={"canales": ["Online"]})
    assert [r["product_key"] for r in result["producto"]] == [1, 2]
    assert [r["ingresos_usd"] for r in result["producto"]] == [Decimal("100")] * 2
