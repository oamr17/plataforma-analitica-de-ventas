"""Intercalados deterministas y fallos reales de transacción con datos sintéticos."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import psycopg
import pytest

from sales_analytics import audit, load
from sales_analytics.db import connect_database


def test_two_publishers_and_retry_cannot_double_publish(batch, next_batch, monkeypatch):
    run, env, path, _ = batch
    second, _ = next_batch()
    entered, release = Event(), Event()
    original = load._write_dimensions

    def paused(conn, candidate):
        original(conn, candidate)
        entered.set()
        assert release.wait(15), "El test no liberó el primer publicador."

    monkeypatch.setattr(load, "_write_dimensions", paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(load.publish_run, run, environ=env)
        assert entered.wait(15)
        try:
            with pytest.raises(load.PublicationFailed):
                executor.submit(
                    load.publish_run,
                    second,
                    environ=env,
                    diagnostics_dir=path / "diagnostics",
                ).result(timeout=10)
            with connect_database("writer", environ=env) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM dw.fact_ventas"
                ).fetchone() == (0,)
        finally:
            release.set()
        assert first.result(timeout=15)["publication_id"] == 1
    assert load.publish_run(second, environ=env)["estado"] == "sin_cambios"
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ops.ejecuciones WHERE publication_id IS NOT NULL"
        ).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (1,)


@pytest.mark.parametrize("stage", ["_write_dimensions", "_write_rates", "_write_facts"])
def test_rollback_after_each_write_preserves_previous_version(
    batch, next_batch, monkeypatch, stage
):
    first, env, path, _ = batch
    load.publish_run(first, environ=env)
    changed, _ = next_batch(
        {
            "Customers.csv": lambda rows: rows[0].update(City="Cambio"),
            "Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$3.00"}),
            "Exchange_Rates.csv": lambda rows: rows.append(
                dict(rows[0], Currency="EUR", Exchange="0.8")
            ),
        },
        authorize=True,
    )
    original = getattr(load, stage)

    def failed(conn, *args):
        original(conn, *args)
        raise psycopg.OperationalError("fixture-only-secret")

    monkeypatch.setattr(load, stage, failed)
    with pytest.raises(load.PublicationFailed) as error:
        load.publish_run(changed, environ=env, diagnostics_dir=path / "diagnostics")
    assert "fixture-only-secret" not in str(error.value)
    with connect_database("writer", environ=env) as conn:
        assert conn.execute("SELECT ciudad FROM dw.dim_cliente").fetchone() == (
            "Ciudad",
        )
        assert conn.execute("SELECT count(*) FROM dw.tipos_cambio").fetchone() == (1,)
        assert conn.execute(
            "SELECT sum(cantidad*precio_referencia_usd)::text, min(run_id::text) "
            "FROM dw.fact_ventas"
        ).fetchone() == ("22.00", str(first))
        assert conn.execute(
            "SELECT max(publication_id) FROM ops.ejecuciones"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND regla='C5'",
            (changed,),
        ).fetchone() == (1,)


def test_database_diagnostic_unavailable_reports_only_local_evidence(
    batch, monkeypatch
):
    run, env, path, _ = batch

    def failed(*args):
        raise psycopg.OperationalError("private-fixture-secret")

    @contextmanager
    def audit_unavailable(*args, **kwargs):
        raise ConnectionError("private-fixture-secret")
        yield

    monkeypatch.setattr(load, "_write_facts", failed)
    monkeypatch.setattr(audit, "connect_database", audit_unavailable)
    with pytest.raises(load.PublicationFailed) as error:
        load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
    saved = path / "diagnostics" / f"{run}.json"
    assert saved.exists() and "ops.incidencias" not in str(error.value)
    assert "private-fixture-secret" not in saved.read_text() + str(error.value)
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND regla='C5'",
            (run,),
        ).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (0,)


def test_publication_owner_is_durable_before_analytical_writes(batch, monkeypatch):
    run, env, _, _ = batch
    original = load._write_dimensions

    def inspect(conn, candidate):
        with connect_database("writer", environ=env) as reader:
            assert reader.execute(
                "SELECT fase,pid FROM ops.ejecuciones WHERE run_id=%s", (run,)
            ).fetchone() == ("publicacion", os.getpid())
        original(conn, candidate)

    monkeypatch.setattr(load, "_write_dimensions", inspect)
    load.publish_run(run, environ=env)


def test_lock_collision_after_claim_is_a_confirmed_failure(batch, monkeypatch):
    run, env, path, _ = batch
    original = load.connect_database
    with connect_database("writer", environ=env) as competitor:

        @contextmanager
        def connection(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                yield conn
            # Intercalado exacto: claim confirmado, aún sin transacción analítica.
            competitor.execute(
                "SELECT pg_advisory_xact_lock(%s)", (load.PUBLICATION_LOCK,)
            )

        monkeypatch.setattr(load, "connect_database", connection)
        with pytest.raises(load.PublicationFailed):
            load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT estado,fase,publication_id FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone() == ("fallido", "publicacion_fallida", None)
        assert conn.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (0,)


def test_uncertain_diagnostic_cannot_overwrite_later_phase(batch):
    run, env, path, _ = batch
    with connect_database("writer", environ=env) as conn:
        audit.claim_phase(conn, run, "validacion_completa", "publicacion")
    location = audit.record_failure(
        run,
        None,
        {},
        {"incierto": True, "regla": "C5", "ordinal": None, "motivo": "Tardío"},
        env,
        path / "diagnostics",
        expected_phase="validacion",
    )
    assert location != "ops.incidencias"
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT fase FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ("publicacion",)


@pytest.mark.parametrize("committed", [False, True])
def test_commit_response_loss_is_resolved_without_repeating_writes(
    batch, monkeypatch, committed
):
    run, env, path, _ = batch
    original = load.connect_database

    @contextmanager
    def lost_reply(*args, **kwargs):
        affected = False
        with original(*args, **kwargs) as conn:
            yield conn
            if conn.execute(
                "SELECT publication_id FROM ops.ejecuciones WHERE run_id=%s", (run,)
            ).fetchone() == (1,):
                affected = True
                if not committed:
                    conn.rollback()
        if affected:
            raise psycopg.OperationalError("fixture: commit reply lost")

    monkeypatch.setattr(load, "connect_database", lost_reply)
    if committed:
        result = load.publish_run(
            run, environ=env, diagnostics_dir=path / "diagnostics"
        )
        assert result["publication_id"] == 1 and result["estado"] == "publicado"
    else:
        with pytest.raises(load.PublicationFailed):
            load.publish_run(run, environ=env, diagnostics_dir=path / "diagnostics")
        saved = json.loads((path / "diagnostics" / f"{run}.json").read_text())
        assert saved["incierto"] is True
    with connect_database("writer", environ=env) as conn:
        assert conn.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (
            int(committed),
        )
        assert conn.execute(
            "SELECT publication_id FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ((1 if committed else None),)


@pytest.mark.parametrize("uncertain", [False, True])
def test_late_failure_preserves_terminal_publication(batch, uncertain):
    run, env, path, _ = batch
    load.publish_run(run, environ=env)
    location = audit.record_failure(
        run,
        None,
        {},
        {"incierto": uncertain, "regla": "C5", "ordinal": None, "motivo": "Tardío"},
        env,
        path / "diagnostics",
    )
    assert location != "ops.incidencias"
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT estado,fase,publication_id FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone() == ("publicado", "publicacion_completa", 1)
