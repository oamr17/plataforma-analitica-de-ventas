"""Publicación real con fixtures pequeñas, solo en sales_analytics_e6_test."""

from decimal import Decimal
from shutil import copyfile
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from sales_analytics import audit, load
from sales_analytics.db import connect_database
from sales_analytics.extract import SOURCES, extract_sources
from sales_analytics.validation_run import read_staging, validate_run


def state(env, run):
    with connect_database("writer", environ=env) as conn:
        return conn.execute(
            "SELECT estado,fase,publication_id FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone()


def analytical_rows(env):
    with connect_database("writer", environ=env) as conn:
        return {
            table: conn.execute(
                sql.SQL("SELECT * FROM dw.{} ORDER BY 1").format(sql.Identifier(table))
            ).fetchall()
            for table in (
                "fact_ventas",
                "tipos_cambio",
                "dim_cliente",
                "dim_producto",
                "dim_sucursal",
                "dim_fecha",
            )
        }


def test_first_publication_resolves_keys_and_keeps_staging(batch):
    run, env, _, _ = batch
    with connect_database("writer", environ=env) as conn:
        before = read_staging(conn, run)
    result = load.publish_run(run, environ=env)
    assert result["estado"] == "publicado" and result["publication_id"] == 1
    assert state(env, run) == ("publicado", "publicacion_completa", 1)
    with connect_database("writer", environ=env) as conn:
        assert read_staging(conn, run) == before
        assert conn.execute(
            "SELECT count(*),sum(cantidad),sum(cantidad*precio_referencia_usd),"
            "sum(cantidad*costo_referencia_usd) FROM dw.fact_ventas"
        ).fetchone() == (1, 11, Decimal("22.00"), Decimal("11.00"))
        assert conn.execute(
            "SELECT c.customer_key,p.product_key,s.store_key,c.codigo_estado,"
            "p.subcategoria_codigo,s.superficie_m2 FROM dw.fact_ventas f "
            "JOIN dw.dim_cliente c USING(cliente_id) "
            "JOIN dw.dim_producto p USING(producto_id) "
            "JOIN dw.dim_sucursal s USING(sucursal_id)"
        ).fetchall() == [(1, 1, 0, "NA", "0101", None)]
        assert conn.execute(
            "SELECT count(*) FROM pg_views WHERE schemaname='marts'"
        ).fetchone() == (4,)


def test_identity_and_rows_not_visible_before_commit(batch, monkeypatch):
    run, env, _, _ = batch
    original = audit.record_publication
    observations = []

    def observe(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        assert conn.execute(
            "SELECT publication_id FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == (1,)
        with connect_database("writer", environ=env) as reader:
            observations.append(
                reader.execute("SELECT count(*) FROM dw.fact_ventas").fetchone()
            )
            assert reader.execute(
                "SELECT publication_id FROM ops.ejecuciones WHERE run_id=%s", (run,)
            ).fetchone() == (None,)

    monkeypatch.setattr(audit, "record_publication", observe)
    load.publish_run(run, environ=env)
    assert observations == [(0,)]
    assert state(env, run)[2] == 1


def test_error_after_candidate_identity_rolls_back_and_audits(batch, monkeypatch):
    run, env, path, _ = batch
    original = audit.record_publication

    def fail(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        raise psycopg.OperationalError("fixture-secret-never-log")

    monkeypatch.setattr(audit, "record_publication", fail)
    with pytest.raises(load.PublicationFailed) as error:
        load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
    assert "fixture-secret" not in str(error.value)
    assert all(not rows for rows in analytical_rows(env).values())
    assert state(env, run) == ("fallido", "publicacion_fallida", None)
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND regla='C5'",
            (run,),
        ).fetchone() == (1,)


def test_identical_new_attempt_and_repeated_call_are_innocuous(batch):
    run, env, _, other_runs = batch
    load.publish_run(run, environ=env)
    before = analytical_rows(env)
    again = load.publish_run(run, environ=env)
    assert again["publication_id"] == 1
    other = extract_sources(environ=env)
    other_runs.append(other)
    assert validate_run(other, environ=env)["apto"]
    result = load.publish_run(other, environ=env)
    assert result["estado"] == "sin_cambios" and result["publication_id"] is None
    assert state(env, other) == ("sin_cambios", "sin_cambios", None)
    assert analytical_rows(env) == before
    assert load.publish_run(other, environ=env) == result


def test_unknown_run_is_not_recreated(batch):
    _, env, _, _ = batch
    with pytest.raises(ValueError, match="intento"):
        load.publish_run(uuid4(), environ=env)


def test_changed_staging_after_e4_is_blocked(batch):
    run, env, path, _ = batch
    with connect_database("writer", environ=env) as conn:
        conn.execute(
            "UPDATE staging.sales SET \"Quantity\"='12' WHERE run_id=%s", (run,)
        )
    with pytest.raises(load.PublicationFailed):
        load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
    assert all(not rows for rows in analytical_rows(env).values())


def test_publication_lock_blocks_without_damaging_validated_attempt(batch):
    run, env, path, _ = batch
    with connect_database("writer", environ=env) as owner:
        owner.execute("SELECT pg_advisory_xact_lock(%s)", (load.PUBLICATION_LOCK,))
        with pytest.raises(load.PublicationFailed):
            load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
        assert owner.execute(
            "SELECT estado,fase FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ("en_curso", "validacion_completa")
    assert (path / "diagnostics" / f"{run}.json").exists()


def test_conflict_rechecked_after_e4_preserves_previous_publication(batch):
    run, env, path, other_runs = batch
    alternate = path / "alternate"
    alternate.mkdir()
    for name in SOURCES:
        copyfile(path / name, alternate / name)
    products = alternate / "Products.csv"
    products.write_bytes(products.read_bytes().replace(b"$2.00", b"$3.00"))
    second_env = type(env)(env)
    second_env["SALES_DATA_DIR"] = str(alternate)
    other = extract_sources(environ=second_env)
    other_runs.append(other)
    assert validate_run(other, environ=env)["apto"]
    load.publish_run(run, environ=env)
    before = analytical_rows(env)
    with pytest.raises(load.PublicationFailed):
        load.publish_run(other, environ=env, diagnostics_dir=path / "diagnostics")
    assert analytical_rows(env) == before and state(env, run)[2] == 1
    assert state(env, other) == ("fallido", "publicacion_fallida", None)
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT regla FROM ops.incidencias WHERE run_id=%s "
            "AND clasificacion='error_critico'",
            (other,),
        ).fetchall() == [("C6",)]


def test_failed_no_change_audit_preserves_previous_publication(batch, monkeypatch):
    run, env, path, other_runs = batch
    load.publish_run(run, environ=env)
    before = analytical_rows(env)
    other = extract_sources(environ=env)
    other_runs.append(other)
    validate_run(other, environ=env)
    original = audit.record_publication

    def fail(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        raise psycopg.OperationalError("fixture")

    monkeypatch.setattr(audit, "record_publication", fail)
    with pytest.raises(load.PublicationFailed):
        load.publish_run(other, environ=env, diagnostics_dir=path / "diagnostics")
    assert analytical_rows(env) == before and state(env, run)[2] == 1
    assert state(env, other) == ("fallido", "publicacion_fallida", None)


def test_bad_candidate_totals_roll_back_before_publication(batch, monkeypatch):
    run, env, path, _ = batch
    original = load._write_candidate

    def wrong_quantity(conn, *args):
        original(conn, *args)
        conn.execute("UPDATE dw.fact_ventas SET cantidad=cantidad+1")

    monkeypatch.setattr(load, "_write_candidate", wrong_quantity)
    with pytest.raises(load.PublicationFailed):
        load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
    assert all(not rows for rows in analytical_rows(env).values())
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT regla FROM ops.incidencias WHERE run_id=%s "
            "AND clasificacion='error_critico'",
            (run,),
        ).fetchall() == [("C4",)]


def test_unknown_published_transform_version_cannot_be_no_change(batch):
    run, env, path, other_runs = batch
    load.publish_run(run, environ=env)
    other = extract_sources(environ=env)
    other_runs.append(other)
    validate_run(other, environ=env)
    with connect_database("writer", environ=env) as conn:
        conn.execute(
            "UPDATE ops.ejecuciones SET conteos=jsonb_set(conteos,"
            "'{publicacion,version_transformacion}','\"unknown\"') WHERE run_id=%s",
            (run,),
        )
    with pytest.raises(load.PublicationFailed):
        load.publish_run(other, environ=env, diagnostics_dir=path / "diagnostics")
    assert state(env, other)[2] is None
