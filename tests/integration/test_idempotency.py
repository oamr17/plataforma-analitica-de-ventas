"""Cambios y replay exclusivamente sintéticos en la base aislada E6."""

import pytest

from sales_analytics import audit, load
from sales_analytics.db import connect_database
from sales_analytics.validation_run import read_staging, validate_run


def test_authorized_type1_keeps_surrogate_keys_and_fact_lineage(batch, next_batch):
    first, env, _, _ = batch
    load.publish_run(first, environ=env)
    with connect_database("writer", environ=env) as conn:
        keys = conn.execute(
            "SELECT cliente_id,producto_id,sucursal_id,run_id FROM dw.fact_ventas"
        ).fetchone()
    run, _ = next_batch(
        {"Customers.csv": lambda rows: rows[0].update(City="Ciudad corregida")},
        authorize=True,
    )
    assert load.publish_run(run, environ=env)["publication_id"] == 2
    with connect_database("writer", environ=env) as conn:
        assert (
            conn.execute(
                "SELECT cliente_id,producto_id,sucursal_id,run_id FROM dw.fact_ventas"
            ).fetchone()
            == keys
        )
        assert conn.execute("SELECT ciudad FROM dw.dim_cliente").fetchone() == (
            "Ciudad corregida",
        )


def test_authorized_revaluation_updates_every_affected_line_atomically(
    batch, next_batch
):
    first, env, _, _ = batch
    load.publish_run(first, environ=env)
    run, _ = next_batch(
        {
            "Products.csv": lambda rows: rows[0].update(
                {"Unit Price USD": "$3.00", "Unit Cost USD": "$1.50"}
            ),
            "Sales.csv": lambda rows: rows.append(
                dict(rows[0], **{"Order Number": "11", "Quantity": "2"})
            ),
        },
        authorize=True,
    )
    assert load.publish_run(run, environ=env)["publication_id"] == 2
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT "
            "sum(cantidad*precio_referencia_usd)::text,"
            "sum(cantidad*costo_referencia_usd)::text,count(DISTINCT run_id) FROM "
            "dw.fact_ventas"
        ).fetchone() == ("39.00", "19.50", 1)
        assert conn.execute(
            "SELECT DISTINCT run_id FROM dw.fact_ventas"
        ).fetchone() == (run,)
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND regla='W8'",
            (run,),
        ).fetchone() == (1,)


def test_new_attempt_of_old_snapshot_is_not_no_change(batch, next_batch):
    first, env, path, _ = batch
    load.publish_run(first, environ=env)
    changed, _ = next_batch(
        {"Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$3.00"})},
        authorize=True,
    )
    load.publish_run(changed, environ=env)
    replay, _ = next_batch(validate=False)
    result = validate_run(replay, environ=env)
    assert not result["apto"] and result["por_regla"]["C6"] > 0
    with pytest.raises(ValueError):
        load.publish_run(replay, environ=env, diagnostics_dir=path / "diagnostics")
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT max(publication_id) FROM ops.ejecuciones"
        ).fetchone() == (2,)


def test_old_terminal_attempt_never_republishes_after_new_version(batch, next_batch):
    first, env, _, _ = batch
    load.publish_run(first, environ=env)
    changed, _ = next_batch(
        {"Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$3.00"})},
        authorize=True,
    )
    load.publish_run(changed, environ=env)
    result = load.publish_run(first, environ=env)
    assert result["publication_id"] == 1 and result["publication_id_vigente"] == 2
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT sum(cantidad*precio_referencia_usd)::text FROM dw.fact_ventas"
        ).fetchone() == ("33.00",)


def test_authorization_against_old_version_is_rechecked_under_lock(batch, next_batch):
    first, env, path, _ = batch
    load.publish_run(first, environ=env)
    second, _ = next_batch(
        {"Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$3.00"})},
        authorize=True,
    )
    stale, _ = next_batch(
        {"Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$4.00"})},
        authorize=True,
    )
    load.publish_run(second, environ=env)
    with pytest.raises(load.PublicationFailed):
        load.publish_run(stale, environ=env, diagnostics_dir=path / "diagnostics")
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT regla FROM ops.incidencias WHERE run_id=%s AND "
            "clasificacion='error_critico'",
            (stale,),
        ).fetchall() == [("C6",)]
        assert conn.execute(
            "SELECT max(publication_id) FROM ops.ejecuciones"
        ).fetchone() == (2,)
        assert conn.execute(
            "SELECT sum(cantidad*precio_referencia_usd)::text FROM dw.fact_ventas"
        ).fetchone() == ("33.00",)


def test_revaluation_identity_and_amounts_become_visible_together(
    batch, next_batch, monkeypatch
):
    first, env, _, _ = batch
    load.publish_run(first, environ=env)
    second, _ = next_batch(
        {"Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$3.00"})},
        authorize=True,
    )
    with connect_database("writer", environ=env) as conn:
        before = read_staging(conn, second)
    original = audit.record_publication

    def inspect(conn, *args):
        original(conn, *args)
        with connect_database("writer", environ=env) as observer:
            assert observer.execute(
                "SELECT max(publication_id) FROM ops.ejecuciones"
            ).fetchone() == (1,)
            assert observer.execute(
                "SELECT sum(cantidad*precio_referencia_usd)::text FROM dw.fact_ventas"
            ).fetchone() == ("22.00",)
        assert conn.execute(
            "SELECT max(publication_id) FROM ops.ejecuciones"
        ).fetchone() == (2,)

    monkeypatch.setattr(audit, "record_publication", inspect)
    load.publish_run(second, environ=env)
    with connect_database("writer", environ=env) as conn:
        assert read_staging(conn, second) == before
        assert conn.execute(
            "SELECT max(publication_id) FROM ops.ejecuciones"
        ).fetchone() == (2,)
        assert conn.execute(
            "SELECT sum(cantidad*precio_referencia_usd)::text FROM dw.fact_ventas"
        ).fetchone() == ("33.00",)
