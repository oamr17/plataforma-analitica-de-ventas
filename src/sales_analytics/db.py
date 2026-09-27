"""Conexiones explícitas por rol, sin pool ni recursos compartidos."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import psycopg

from sales_analytics.config import load_database_url

PUBLICATION_LOCK = 53415001


def acquire_publication_lock(connection: psycopg.Connection) -> None:
    """Misma exclusión para publicar y reconciliar; sin esperas indefinidas."""
    if not connection.execute(
        "SELECT pg_try_advisory_xact_lock(%s)", (PUBLICATION_LOCK,)
    ).fetchone()[0]:
        raise psycopg.errors.LockNotAvailable("Publicador ocupado.")


@contextmanager
def connect_database(
    role: str, *, environ: Mapping[str, str] | None = None
) -> Iterator[psycopg.Connection]:
    """Abre una conexión por uso; confirma o revierte y siempre cierra al salir."""
    url = load_database_url(role, environ)
    try:
        connection = psycopg.connect(
            url, connect_timeout=5, application_name=f"sales_analytics_{role}"
        )
    except psycopg.OperationalError:
        raise ConnectionError(
            f"No se pudo conectar el rol {role}; revisar servidor y credenciales."
        ) from None
    with connection:
        connection.read_only = role == "reader"
        yield connection
