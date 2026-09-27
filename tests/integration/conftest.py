"""Fixtures sintéticas, transaccionales y restringidas a la base de pruebas."""

import csv
import os
from pathlib import Path
from shutil import copyfile
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from sales_analytics.db import connect_database
from sales_analytics.extract import BUSINESS_FIELDS, SOURCES, extract_sources
from sales_analytics.validation import validate_records
from sales_analytics.validation_run import read_staging, validate_run


class PrivateUrls(dict):
    """Evita que pytest muestre credenciales al representar argumentos fallidos."""

    def __repr__(self):
        return "<credenciales de integración ocultas>"


@pytest.fixture(scope="session")
def urls():
    result = PrivateUrls()
    for role, user in (("writer", "sales_e6_etl"), ("reader", "sales_e6_reader")):
        name = f"SALES_TEST_{role.upper()}_DATABASE_URL"
        value = os.environ.get(name)
        if not value:
            pytest.fail(f"Falta {name}; no se omiten las pruebas de integración.")
        params = conninfo_to_dict(value)
        if (
            params.get("dbname") != "sales_analytics_e6_test"
            or params.get("user") != user
        ):
            pytest.fail(
                "Se rechazó una conexión que no corresponde a la base de pruebas."
            )
        with psycopg.connect(value, connect_timeout=5) as conn:
            assert conn.execute(
                "SELECT current_database(), current_user"
            ).fetchone() == ("sales_analytics_e6_test", user)
        result[f"SALES_{role.upper()}_DATABASE_URL"] = value
    return result


@pytest.fixture
def writer(urls):
    conn = psycopg.connect(urls["SALES_WRITER_DATABASE_URL"], connect_timeout=5)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def run_id(writer):
    value = uuid4()
    writer.execute(
        "INSERT INTO ops.ejecuciones "
        "(run_id, version_reglas, equipo, pid, inicio_proceso) "
        "VALUES (%s, 'fixture-e2', 'test', 1, CURRENT_TIMESTAMP)",
        (value,),
    )
    return value


@pytest.fixture
def dimension_ids(writer, run_id):
    writer.execute(
        "INSERT INTO dw.dim_fecha (fecha) VALUES ('2020-01-01'), ('2020-01-02')"
    )
    customer = writer.execute(
        "INSERT INTO dw.dim_cliente (customer_key, fecha_nacimiento) "
        "VALUES (1, '1990-01-01') RETURNING cliente_id"
    ).fetchone()[0]
    product = writer.execute(
        "INSERT INTO dw.dim_producto "
        "(product_key, precio_catalogo_usd, costo_catalogo_usd) "
        "VALUES (1, 10, 12) RETURNING producto_id"
    ).fetchone()[0]
    store = writer.execute(
        "INSERT INTO dw.dim_sucursal (store_key, canal, fecha_apertura) "
        "VALUES (0, 'Online', '2010-01-01') RETURNING sucursal_id"
    ).fetchone()[0]
    writer.execute(
        "INSERT INTO dw.tipos_cambio (fecha, moneda, tasa_original) "
        "VALUES ('2020-01-01', 'USD', 1)"
    )
    return customer, product, store


@pytest.fixture
def sale(writer, run_id, dimension_ids):
    customer, product, store = dimension_ids
    writer.execute(
        "INSERT INTO dw.fact_ventas (numero_pedido, linea_pedido, fecha_pedido, "
        "cliente_id, producto_id, sucursal_id, cantidad, moneda_pedido, "
        "precio_referencia_usd, costo_referencia_usd, run_id) "
        "VALUES (1, 1, '2020-01-01', %s, %s, %s, 11, 'USD', 10, 12, %s)",
        (customer, product, store, run_id),
    )


@pytest.fixture
def staged_run(urls, valid_rows, tmp_path):
    dictionary = []
    for table, fields in BUSINESS_FIELDS.items():
        name = table.replace(" ", "_") + ".csv"
        with (tmp_path / name).open(
            "w", encoding="cp1252" if table == "Customers" else "utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows([[r[f] for f in fields] for r in valid_rows[name]])
        dictionary.extend((table, f, "Definición sintética") for f in fields)
    with (tmp_path / "Data_Dictionary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["Table", "Field", "Description"])
        writer.writerows(dictionary)
    env = type(urls)(urls)
    env["SALES_DATA_DIR"] = str(tmp_path)
    run = extract_sources(environ=env)
    yield run, env, tmp_path
    with connect_database("writer", environ=urls) as conn:
        for table, _, _ in SOURCES.values():
            if table:
                conn.execute(
                    sql.SQL("DELETE FROM staging.{} WHERE run_id=%s").format(
                        sql.Identifier(table)
                    ),
                    (run,),
                )
        conn.execute("DELETE FROM ops.incidencias WHERE run_id=%s", (run,))
        conn.execute("DELETE FROM ops.ejecuciones WHERE run_id=%s", (run,))


@pytest.fixture
def batch(staged_run):
    run, env, path = staged_run
    with connect_database("writer", environ=env) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == (
            "sales_analytics_e6_test",
        )
        for table in (
            "fact_ventas",
            "tipos_cambio",
            "dim_cliente",
            "dim_producto",
            "dim_sucursal",
            "dim_fecha",
        ):
            assert conn.execute(
                sql.SQL("SELECT count(*) FROM dw.{}").format(sql.Identifier(table))
            ).fetchone() == (0,), (
                "La suite sintética E5 exige DW vacío; no borra datos existentes."
            )
    validate_run(run, environ=env)
    other_runs = []
    yield run, env, path, other_runs
    with connect_database("writer", environ=env) as conn:
        # El DW estaba vacío; únicamente estas fixtures publicaron durante la prueba.
        assert {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT run_id FROM dw.fact_ventas"
            ).fetchall()
        } <= {run, *other_runs}
        for table in (
            "fact_ventas",
            "tipos_cambio",
            "dim_cliente",
            "dim_producto",
            "dim_sucursal",
            "dim_fecha",
        ):
            conn.execute(sql.SQL("DELETE FROM dw.{}").format(sql.Identifier(table)))
        for other in other_runs:
            for table, _, _ in SOURCES.values():
                if table:
                    conn.execute(
                        sql.SQL("DELETE FROM staging.{} WHERE run_id=%s").format(
                            sql.Identifier(table)
                        ),
                        (other,),
                    )
            conn.execute("DELETE FROM ops.incidencias WHERE run_id=%s", (other,))
            conn.execute("DELETE FROM ops.ejecuciones WHERE run_id=%s", (other,))


@pytest.fixture
def next_batch(batch):
    first, env, path, others = batch

    def create(changes=None, *, authorize=False, validate=True):
        folder = path / f"next-{len(others)}"
        folder.mkdir()
        for name, (_, _, encoding) in SOURCES.items():
            copyfile(Path(env["SALES_DATA_DIR"]) / name, folder / name)
            if changes and name in changes:
                with (folder / name).open(encoding=encoding, newline="") as stream:
                    reader = csv.DictReader(stream)
                    fields = reader.fieldnames
                    rows = list(reader)
                changes[name](rows)
                with (folder / name).open("w", encoding=encoding, newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
        new_env = type(env)(env)
        new_env["SALES_DATA_DIR"] = str(folder)
        run = extract_sources(environ=new_env)
        others.append(run)
        auth = {}
        if authorize:
            with connect_database("writer", environ=env) as conn:
                prior = conn.execute(
                    "SELECT run_id FROM ops.ejecuciones WHERE publication_id IS NOT "
                    "NULL ORDER BY publication_id DESC LIMIT 1"
                ).fetchone()
                analysis = validate_records(
                    read_staging(conn, run), previous=read_staging(conn, prior[0])
                )
                auth = {
                    i["evidencia"]["cambio_id"]: "aprobacion-sintetica-E6"
                    for i in analysis["incidencias"]
                    if i["regla"] == "C6"
                }
        if validate:
            result = validate_run(run, environ=env, authorizations=auth)
            assert result["apto"]
        return run, new_env

    return create
