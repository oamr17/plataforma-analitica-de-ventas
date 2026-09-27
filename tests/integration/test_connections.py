"""Autenticación, cierre y privilegios sobre PostgreSQL real."""

import socket
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from sales_analytics import db


@pytest.mark.parametrize("role", ["writer", "reader"])
def test_connection_is_closed_on_success(urls, role):
    with db.connect_database(role, environ=urls) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == (
            "sales_analytics_e6_test",
        )
        assert not conn.closed
    assert conn.closed


def test_connection_is_closed_on_error(urls):
    with pytest.raises(RuntimeError, match="fixture"):
        with db.connect_database("writer", environ=urls) as conn:
            raise RuntimeError("fixture")
    assert conn.closed


def test_incorrect_credentials_rejected(urls):
    # La contraseña sintética solo existe en memoria; no se registra el DSN real.
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            urls["SALES_WRITER_DATABASE_URL"],
            password="intentionally-invalid-e2",
            connect_timeout=2,
        )


def test_unavailable_server_rejected(urls):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        # Reservar sin listen evita competir con un servidor real en ese puerto.
        port = reserved.getsockname()[1]
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(
                urls["SALES_WRITER_DATABASE_URL"], port=port, connect_timeout=2
            )


def test_connection_boundary_hides_credentials(urls):
    invalid = type(urls)(urls)
    from urllib.parse import urlsplit, urlunsplit

    uri = urlsplit(invalid["SALES_WRITER_DATABASE_URL"])
    invalid["SALES_WRITER_DATABASE_URL"] = urlunsplit(
        (
            uri.scheme,
            f"{uri.username}:invalid-secret@{uri.hostname}:{uri.port}",
            uri.path,
            "",
            "",
        )
    )
    with pytest.raises(ConnectionError) as captured:
        with db.connect_database("writer", environ=invalid):
            pytest.fail("Se aceptó una credencial incorrecta")
    assert "invalid-secret" not in str(captured.value)


@pytest.mark.parametrize("role", ["writer", "reader"])
@pytest.mark.parametrize("database", ["sales_analytics", "sales_analytics_test"])
def test_test_roles_cannot_connect_to_project_database(urls, role, database):
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            make_conninfo(urls[f"SALES_{role.upper()}_DATABASE_URL"], dbname=database),
            connect_timeout=2,
        )


def test_writer_can_insert_update_delete_and_use_identity(writer, run_id):
    identifier = writer.execute(
        "INSERT INTO ops.incidencias (run_id, regla, clasificacion, motivo) "
        "VALUES (%s, 'C1', 'error_critico', 'fixture') RETURNING incidencia_id",
        (run_id,),
    ).fetchone()[0]
    writer.execute(
        "UPDATE ops.incidencias SET motivo='actualizado' WHERE incidencia_id=%s",
        (identifier,),
    )
    assert writer.execute(
        "SELECT motivo FROM ops.incidencias WHERE incidencia_id=%s", (identifier,)
    ).fetchone() == ("actualizado",)
    writer.execute("DELETE FROM ops.incidencias WHERE incidencia_id=%s", (identifier,))
    assert writer.execute(
        "SELECT count(*) FROM ops.incidencias WHERE incidencia_id=%s", (identifier,)
    ).fetchone() == (0,)


def test_reader_can_read_only_authorized_metadata(urls):
    with db.connect_database("reader", environ=urls) as conn:
        assert conn.execute("SELECT count(*) FROM ops.ejecuciones").fetchone()[0] >= 0
        assert conn.execute("SHOW transaction_read_only").fetchone() == ("on",)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ops.ejecuciones (run_id) VALUES (NULL)",
        "UPDATE ops.ejecuciones SET fase='forbidden'",
        "DELETE FROM ops.ejecuciones",
        "TRUNCATE ops.incidencias",
        "ALTER TABLE ops.incidencias ADD COLUMN forbidden text",
        "DROP TABLE ops.incidencias",
        "CREATE TABLE marts.forbidden (id integer)",
        "CREATE TABLE public.forbidden (id integer)",
        "CREATE SCHEMA forbidden",
        "CREATE TEMP TABLE forbidden (id integer)",
        "ALTER SCHEMA ops RENAME TO forbidden",
        "SET ROLE sales_e6_etl",
        "SELECT * FROM staging.sales",
        "SELECT * FROM dw.fact_ventas",
    ],
)
def test_reader_denied_even_without_read_only_transaction(urls, statement):
    # Conexión directa: verifica GRANT/ownership, no solo read_only del wrapper.
    with psycopg.connect(urls["SALES_READER_DATABASE_URL"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(statement)


@pytest.mark.parametrize("role", ["writer", "reader"])
def test_roles_are_not_administrators(urls, role):
    with psycopg.connect(urls[f"SALES_{role.upper()}_DATABASE_URL"]) as conn:
        assert (
            conn.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, "
                "rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            == (False,) * 5
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                sql.SQL("CREATE SCHEMA {}").format(
                    sql.Identifier("forbidden_" + uuid4().hex)
                )
            )
