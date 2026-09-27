"""Snapshot aprobado completo; requiere originales locales y base sintética vacía."""

import csv
import hashlib
import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from shutil import copyfile

import psycopg
import pytest
from psycopg import sql
from streamlit.testing.v1 import AppTest

from sales_analytics import analytics, audit, load
from sales_analytics.db import connect_database
from sales_analytics.extract import SOURCES, extract_sources
from sales_analytics.read_dashboard import read_dashboard
from sales_analytics.validation_run import validate_run

ROOT = Path(__file__).resolve().parents[2]
DW_TABLES = (
    "fact_ventas",
    "tipos_cambio",
    "dim_cliente",
    "dim_producto",
    "dim_sucursal",
    "dim_fecha",
)


def fingerprints(conn, schema, tables, run=None):
    """Compara contenido y claves sin mostrar datos personales en una aserción."""
    result = {}
    for table in tables:
        query = sql.SQL("SELECT to_jsonb(t)::text FROM {}.{} t ").format(
            sql.Identifier(schema), sql.Identifier(table)
        )
        if run is not None:
            query += sql.SQL("WHERE run_id=%s ")
        query += sql.SQL("ORDER BY to_jsonb(t)::text")
        rows = conn.execute(query, (run,) if run is not None else ()).fetchall()
        result[table] = (
            len(rows),
            hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest(),
        )
    return result


@pytest.fixture
def clean_snapshot_database(urls):
    tables = [table for table, _, _ in SOURCES.values() if table]
    with connect_database("writer", environ=urls) as conn:
        # urls verifica base y roles; además exige ausencia de evidencia previa.
        for schema, names in (
            ("dw", DW_TABLES),
            ("staging", tables),
            ("ops", ("incidencias", "ejecuciones")),
        ):
            for name in names:
                assert (
                    conn.execute(
                        sql.SQL("SELECT count(*) FROM {}.{}").format(
                            sql.Identifier(schema), sql.Identifier(name)
                        )
                    ).fetchone()[0]
                    == 0
                ), "E9 requiere base aislada vacía; no se vacía al iniciar."
    owned = []
    yield owned
    with connect_database("writer", environ=urls) as conn:
        assert {
            row[0] for row in conn.execute("SELECT run_id FROM ops.ejecuciones")
        } <= set(owned)
        for table in DW_TABLES:
            conn.execute(sql.SQL("DELETE FROM dw.{}").format(sql.Identifier(table)))
        for schema, names in (
            ("staging", tables),
            ("ops", ("incidencias", "ejecuciones")),
        ):
            for name in names:
                conn.execute(
                    sql.SQL("DELETE FROM {}.{} WHERE run_id=ANY(%s)").format(
                        sql.Identifier(schema), sql.Identifier(name)
                    ),
                    (owned,),
                )


def test_original_snapshot_end_to_end(
    urls, clean_snapshot_database, tmp_path, monkeypatch
):
    originals = Path(os.environ.get("SALES_DATA_DIR", ROOT / "Data")).resolve()
    expected_hashes = dict(
        re.findall(
            r"\| ([\w_]+\.csv) \| ([A-F0-9]{64}) \|",
            (ROOT / "docs/data_profile.md").read_text(encoding="utf-8"),
        )
    )
    assert set(expected_hashes) == set(SOURCES)

    def hashes():
        return {
            name: hashlib.sha256((originals / name).read_bytes()).hexdigest().upper()
            for name in SOURCES
        }

    assert hashes() == expected_hashes, (
        "Se requiere el snapshot identificado en el perfil."
    )
    env = type(urls)(urls)
    env["SALES_DATA_DIR"] = str(originals)
    owned = clean_snapshot_database

    def extract_and_validate(environment):
        run = extract_sources(environ=environment)
        owned.append(run)
        validation = validate_run(run, environ=environment)
        assert validation["apto"]
        assert validation["criticos"] == validation["rechazados"] == 0
        return run

    try:
        first = extract_and_validate(env)
        stage_tables = [table for table, _, _ in SOURCES.values() if table]
        with connect_database("writer", environ=env) as conn:
            manifest = conn.execute(
                "SELECT archivos FROM ops.ejecuciones WHERE run_id=%s", (first,)
            ).fetchone()[0]
            assert {
                name: entry["sha256"].upper() for name, entry in manifest.items()
            } == expected_hashes
            assert all(entry["estado"] == "confirmado" for entry in manifest.values())
            assert manifest["Data_Dictionary.csv"]["registros"] == 37
            staging_before = fingerprints(conn, "staging", stage_tables, first)
            assert {name: row[0] for name, row in staging_before.items()} == {
                "customers": 15266,
                "products": 2517,
                "stores": 67,
                "sales": 62884,
                "exchange_rates": 11215,
            }
        published = load.publish_run(first, environ=env)
        assert published["publication_id"] == 1
        with connect_database("writer", environ=env) as conn:
            before = fingerprints(conn, "dw", DW_TABLES)
        bundle = read_dashboard("Resumen ejecutivo", environ=env)
        summary = bundle["resumen"][0]
        assert (summary["lineas"], summary["pedidos"], summary["unidades"]) == (
            62884,
            26326,
            197757,
        )
        assert (
            summary["ingresos_usd"],
            summary["costos_usd"],
            summary["margen_usd"],
        ) == (Decimal("55755479.59"), Decimal("23092791.21"), Decimal("32662688.38"))
        assert bundle["metadatos"]["run_id"] == first
        with connect_database("reader", environ=env) as conn:
            direct = analytics.read_analytics(conn, date(2016, 1, 1), date(2021, 2, 20))
        assert direct["resumen"] == bundle["resumen"]
        assert bundle["calidad"]["totales"] == [
            {
                "run_id": first,
                "errores": 0,
                "rechazos": 0,
                "advertencias": 55512,
            }
        ]
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        app = AppTest.from_file(str(ROOT / "src/sales_analytics/dashboard.py")).run(
            timeout=70
        )
        assert not app.exception and not app.error
        assert {m.label: m.value for m in app.metric} == {
            "Ingresos estimados": "USD 55.755.479,59",
            "Pedidos": "26.326",
            "Unidades registradas": "197.757",
            "Ticket promedio estimado": "USD 2.117,89",
            "Margen estimado de catálogo": "USD 32.662.688,38",
            "Margen sobre ingresos": "58,58 %",
        }
        assert app.session_state["ultimo_completo"]["resumen"] == direct["resumen"]

        repeated = extract_and_validate(env)
        result = load.publish_run(repeated, environ=env)
        assert result["estado"] == "sin_cambios" and result["publication_id"] is None

        # La fixture local añade un producto sintético, sin revalorar ninguno real.
        for name in SOURCES:
            copyfile(originals / name, tmp_path / name)
        products = tmp_path / "Products.csv"
        with products.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, new_product = reader.fieldnames, next(reader)
        new_product.update(
            {
                "ProductKey": "999999999",
                "Product Name": "Fixture E9",
                "Unit Price USD": "$1.00",
                "Unit Cost USD": "$0.00",
            }
        )
        with products.open("a", encoding="utf-8", newline="") as stream:
            if not products.read_bytes().endswith(b"\n"):
                stream.write("\n")
            csv.DictWriter(stream, fieldnames=fields).writerow(new_product)
        fixture_env = type(env)(env)
        fixture_env["SALES_DATA_DIR"] = str(tmp_path)
        failed = extract_and_validate(fixture_env)
        original = audit.record_publication

        def fail_before_commit(conn, *args, **kwargs):
            original(conn, *args, **kwargs)
            assert (
                conn.execute(
                    "SELECT count(*) FROM dw.dim_producto WHERE product_key=999999999"
                ).fetchone()[0]
                == 1
            )
            with connect_database("reader", environ=env) as observer:
                assert analytics.read_publication(observer)["publication_id"] == 1
            raise psycopg.OperationalError("Fallo sintético E9 antes del commit")

        monkeypatch.setattr(audit, "record_publication", fail_before_commit)
        with pytest.raises(load.PublicationFailed):
            load.publish_run(
                failed, environ=env, diagnostics_dir=tmp_path / "diagnostics"
            )
        with connect_database("writer", environ=env) as conn:
            assert fingerprints(conn, "dw", DW_TABLES) == before
            assert fingerprints(conn, "staging", stage_tables, first) == staging_before
            assert conn.execute(
                "SELECT estado,publication_id FROM ops.ejecuciones WHERE run_id=%s",
                (failed,),
            ).fetchone() == ("fallido", None)
            assert (
                conn.execute(
                    "SELECT count(*) FROM ops.incidencias "
                    "WHERE run_id=%s AND regla='C5'",
                    (failed,),
                ).fetchone()[0]
                == 1
            )
        after = read_dashboard("Calidad y cargas", environ=env)
        assert after["metadatos"]["publication_id"] == 1
        assert after["calidad"]["ultimo_intento"]["run_id"] == failed
        assert after["resumen"] == bundle["resumen"]
    finally:
        assert hashes() == expected_hashes
