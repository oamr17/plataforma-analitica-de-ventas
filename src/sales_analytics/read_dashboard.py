"""Una instantánea completa por actualización; sin caché ni recursos compartidos."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from time import monotonic

import psycopg
from psycopg import sql

from sales_analytics import analytics
from sales_analytics.db import connect_database

PAGES = ("Resumen ejecutivo", "Productos y sucursales", "Clientes", "Calidad y cargas")
ISOLATION = psycopg.IsolationLevel.REPEATABLE_READ
QUERY_SECONDS = 40
IDLE_SECONDS = 5
CYCLE_SECONDS = 60


def read_dashboard(
    page: str,
    inicio: date | None = None,
    fin: date | None = None,
    *,
    filters: Mapping[str, list | None] | None = None,
    accept_coverage_warnings: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Devuelve datos materializados solo después de cerrar la conexión exclusiva.

    Una excepción invalida todo el ciclo. PostgreSQL 17 limita consultas, inactividad
    y transacción; el reloj del cliente incluye conexión y materialización final.
    """
    started = monotonic()
    if page not in PAGES:
        raise ValueError("Página desconocida.")
    selected = {
        key: None if value is None else list(value)
        for key, value in (filters or {}).items()
    }
    with connect_database("reader", environ=environ) as conn:
        conn.isolation_level = ISOLATION
        remaining = CYCLE_SECONDS - (monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Se agotó el tiempo de actualización.")
        for setting, seconds in (
            ("statement_timeout", QUERY_SECONDS),
            ("idle_in_transaction_session_timeout", IDLE_SECONDS),
            ("transaction_timeout", remaining),
        ):
            conn.execute(
                sql.SQL("SET LOCAL {} = {}").format(
                    sql.Identifier(setting),
                    sql.Literal(f"{max(1, int(seconds * 1000))}ms"),
                )
            )
        publication = analytics.read_publication(conn)
        result = {
            "pagina": page,
            "metadatos": publication,
            "leido_en": datetime.now(UTC),
        }
        if publication:
            options = analytics.read_options(conn)
            for key, values in selected.items():
                if key not in options or (
                    values is not None
                    and any(value not in options[key] for value in values)
                ):
                    raise ValueError(
                        "La selección ya no es válida; corrija los filtros."
                    )
            inicio = inicio if inicio is not None else publication["fecha_pedido_min"]
            fin = fin if fin is not None else publication["fecha_pedido_max"]
            result.update(
                analytics.read_analytics(
                    conn,
                    inicio,
                    fin,
                    filters=selected,
                    accept_coverage_warnings=accept_coverage_warnings,
                )
            )
            result.update(
                metadatos=publication,
                opciones=options,
                calidad=analytics.read_quality(conn, publication["run_id"]),
                seleccion={
                    "inicio": inicio,
                    "fin": fin,
                    "filters": selected,
                    "accept_coverage_warnings": accept_coverage_warnings,
                },
            )
        if monotonic() - started >= CYCLE_SECONDS:
            raise TimeoutError("Se agotó el tiempo de actualización.")
    result["duracion_segundos"] = monotonic() - started
    if result["duracion_segundos"] >= CYCLE_SECONDS:
        raise TimeoutError("Se agotó el tiempo de actualización al cerrar la lectura.")
    return result
