"""Lecturas completas en la base sintética; nunca modifican la aceptación."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from threading import Event
from time import monotonic

import psycopg
import pytest

from sales_analytics import analytics
from sales_analytics import read_dashboard as dashboard
from sales_analytics.db import connect_database
from sales_analytics.load import publish_run


@pytest.fixture
def published(batch):
    run, env, _, _ = batch
    publish_run(run, environ=env)
    return run, env


@pytest.fixture
def connections(monkeypatch):
    opened = []

    @contextmanager
    def tracked(role, **kwargs):
        assert role == "reader"
        with connect_database(role, **kwargs) as conn:
            opened.append(conn)
            yield conn

    monkeypatch.setattr(dashboard, "connect_database", tracked)
    yield opened
    assert all(conn.closed for conn in opened)


def test_no_publication(urls, connections):
    result = dashboard.read_dashboard("Resumen ejecutivo", environ=urls)
    assert result["metadatos"] is None
    assert "resumen" not in result
    assert connections[0].closed


def test_complete_materialized_bundle(published, connections):
    run, env = published
    result = dashboard.read_dashboard("Clientes", environ=env)
    assert result["metadatos"]["run_id"] == run
    assert result["resumen"][0]["ingresos_usd"] == Decimal("22.00")
    assert result["resumen"][0]["nuevos"] == 1
    assert result["resumen"][0]["recurrentes"] == 0
    assert result["opciones"]["store_keys"] == [0]
    assert result["opciones"]["categoria_codigos"] == ["01"]
    assert result["calidad"]["ultimo_intento"]["run_id"] == run
    assert result["calidad"]["totales"][0]["errores"] == 0
    assert result["calidad"]["totales"][0]["rechazos"] == 0
    assert connections[0].closed
    assert result["leido_en"].tzinfo is not None


def test_deadline_includes_closing(published, monkeypatch):
    original = dashboard.monotonic
    closed = False

    @contextmanager
    def close_slowly(role, **kwargs):
        nonlocal closed
        with connect_database(role, **kwargs) as conn:
            yield conn
        closed = True

    monkeypatch.setattr(dashboard, "connect_database", close_slowly)
    monkeypatch.setattr(
        dashboard, "monotonic", lambda: original() + (100 if closed else 0)
    )
    with pytest.raises(TimeoutError):
        dashboard.read_dashboard("Clientes", environ=published[1])


def test_valid_empty_and_invalid_filters(published, connections):
    _, env = published
    result = dashboard.read_dashboard(
        "Resumen ejecutivo", filters={"store_keys": [0]}, environ=env
    )
    assert result["resumen"][0]["pedidos"] == 1
    empty = dashboard.read_dashboard(
        "Resumen ejecutivo", filters={"product_keys": []}, environ=env
    )
    assert empty["resumen"][0]["ingresos_usd"] == 0
    assert empty["resumen"][0]["ticket_usd"] is None
    with pytest.raises(ValueError, match="selección"):
        dashboard.read_dashboard(
            "Resumen ejecutivo", filters={"product_keys": [999]}, environ=env
        )


@pytest.mark.parametrize("isolation", ["REPEATABLE_READ", "READ_COMMITTED"])
def test_publication_between_queries(published, next_batch, monkeypatch, isolation):
    _, env = published
    run_b, env_b = next_batch(
        {
            "Products.csv": lambda rows: rows[0].update(
                {"Unit Price USD": "$3.00", "CategoryKey": "02", "Product Name": "B"}
            ),
            "Sales.csv": lambda rows: rows.append(
                dict(rows[0], **{"Order Number": "11", "Quantity": "1"})
            ),
        },
        authorize=True,
    )
    fixed, committed = Event(), Event()
    original = analytics.read_options

    def interleave(conn):
        fixed.set()
        assert committed.wait(15)
        return original(conn)

    monkeypatch.setattr(analytics, "read_options", interleave)
    # Control negativo: mismo ciclo, cambiando solo el aislamiento en esta prueba.
    monkeypatch.setattr(
        dashboard, "ISOLATION", getattr(psycopg.IsolationLevel, isolation)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dashboard.read_dashboard, "Clientes", environ=env)
        assert fixed.wait(10)
        try:
            publish_run(run_b, environ=env_b)
        finally:
            committed.set()
        first = future.result(timeout=15)
    assert first["metadatos"]["publication_id"] == 1
    expected = "22.00" if isolation == "REPEATABLE_READ" else "36.00"
    assert first["resumen"][0]["ingresos_usd"] == Decimal(expected)
    if isolation == "REPEATABLE_READ":
        assert first["calidad"]["ultimo_intento"]["estado"] == "en_curso"
        assert first["opciones"]["categoria_codigos"] == ["01"]
        assert first["opciones"]["productos"][0]["producto"] == "Producto"
        assert first["resumen"][0]["pedidos"] == 1
    current = dashboard.read_dashboard("Clientes", environ=env)
    assert current["metadatos"]["publication_id"] == 2
    assert current["resumen"][0]["ingresos_usd"] == Decimal("36.00")
    assert current["resumen"][0]["pedidos"] == 2
    assert current["opciones"]["categoria_codigos"] == ["02"]
    with pytest.raises(ValueError, match="selección"):
        dashboard.read_dashboard(
            "Clientes", filters={"categoria_codigos": ["01"]}, environ=env
        )


def test_simultaneous_sessions_are_independent(
    published, next_batch, monkeypatch, connections
):
    _, env = published
    run_b, env_b = next_batch(
        {"Products.csv": lambda rows: rows[0].update({"Unit Price USD": "$3.00"})},
        authorize=True,
    )
    entered, release = Event(), Event()
    original = analytics.read_options
    first_pid = []

    def hold_first(conn):
        if not entered.is_set():
            first_pid.append(conn.info.backend_pid)
            entered.set()
            assert release.wait(10)
        else:
            assert conn.info.backend_pid != first_pid[0]
        return original(conn)

    monkeypatch.setattr(analytics, "read_options", hold_first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dashboard.read_dashboard, "Clientes", environ=env)
        assert entered.wait(5)
        try:
            publish_run(run_b, environ=env_b)
            second = dashboard.read_dashboard("Clientes", environ=env)
            assert connections[1].closed
            assert not connections[0].closed
        finally:
            release.set()
        first = future.result(timeout=10)
    assert first["metadatos"]["publication_id"] == 1
    assert first["resumen"][0]["ingresos_usd"] == Decimal("22.00")
    assert second["metadatos"]["publication_id"] == 2
    assert second["resumen"][0]["ingresos_usd"] == Decimal("33.00")


def test_idle_timeout_closes_connection(published, monkeypatch, connections):
    monkeypatch.setattr(dashboard, "IDLE_SECONDS", 0.05)
    original = analytics.read_options

    def idle(conn):
        # Espera deliberada para probar el límite de inactividad, no concurrencia.
        Event().wait(0.15)
        return original(conn)

    monkeypatch.setattr(analytics, "read_options", idle)
    with pytest.raises(psycopg.Error):
        dashboard.read_dashboard("Clientes", environ=published[1])


@pytest.mark.parametrize("failure", ["query", "cancel", "timeout", "total"])
def test_failure_discards_bundle_and_closes(
    published, monkeypatch, connections, failure
):
    _, env = published

    def fail(conn):
        if failure == "query":
            conn.execute("SELECT 1/0")
        elif failure == "cancel":
            raise KeyboardInterrupt
        else:
            conn.execute("SELECT pg_sleep(2)")

    monkeypatch.setattr(analytics, "read_options", fail)
    if failure == "timeout":
        monkeypatch.setattr(dashboard, "QUERY_SECONDS", 0.05)
    if failure == "total":
        monkeypatch.setattr(dashboard, "CYCLE_SECONDS", 0.1)
    error = KeyboardInterrupt if failure == "cancel" else (psycopg.Error, TimeoutError)
    with pytest.raises(error):
        dashboard.read_dashboard("Clientes", environ=env)
    assert connections[0].closed


def test_invalid_dates(published):
    with pytest.raises(ValueError):
        dashboard.read_dashboard(
            "Clientes", date(2020, 2, 1), date(2020, 1, 1), environ=published[1]
        )


def test_server_cancel_and_no_remaining_backend(published, monkeypatch, connections):
    _, env = published
    entered = Event()
    pid = []

    def blocked(conn):
        pid.append(conn.info.backend_pid)
        entered.set()
        conn.execute("SELECT pg_advisory_xact_lock(882233)")

    monkeypatch.setattr(analytics, "read_options", blocked)
    with connect_database("writer", environ=env) as blocker:
        blocker.execute("SELECT pg_advisory_xact_lock(882233)")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(dashboard.read_dashboard, "Clientes", environ=env)
            assert entered.wait(5)
            # La espera depende del bloqueo observado, nunca de un sleep arbitrario.
            with connect_database("reader", environ=env) as observer:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    if observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks "
                        "WHERE pid=%s AND NOT granted)",
                        (pid[0],),
                    ).fetchone()[0]:
                        break
                else:
                    pytest.fail("La consulta no alcanzó el bloqueo previsto")
            connections[0].cancel()
            with pytest.raises(psycopg.errors.QueryCanceled):
                future.result(timeout=5)
    with connect_database("reader", environ=env) as observer:
        assert (
            observer.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (pid[0],)
            ).fetchone()[0]
            == 0
        )


def test_sql_values_rendered_and_widget_filters(published, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    _, env = published
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(
        str(Path(__file__).parents[2] / "src/sales_analytics/dashboard.py")
    ).run(timeout=20)
    assert not app.exception
    assert {m.label: m.value for m in app.metric}["Ingresos estimados"] == "USD 22,00"
    app.multiselect(key="store_keys").set_value([0])
    app.multiselect(key="canales").set_value(["Online"])
    app.button[0].click().run(timeout=20)
    assert not app.exception
    assert app.session_state["ultimo_completo"]["seleccion"]["filters"][
        "store_keys"
    ] == [0]
    assert {m.label: m.value for m in app.metric}[
        "Ticket promedio estimado"
    ] == "USD 22,00"
    app.date_input(key="inicio").set_value(date(2021, 2, 1))
    app.date_input(key="fin").set_value(date(2021, 2, 20))
    app.button[0].click().run(timeout=20)
    assert not app.exception
    assert {m.label: m.value for m in app.metric}["Ingresos estimados"] == "USD 0,00"
    assert {m.label: m.value for m in app.metric}["Ticket promedio estimado"] == "—"
