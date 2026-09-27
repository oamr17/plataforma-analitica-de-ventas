"""Publicación atómica, cambios autorizados y repetición del vigente."""

from collections.abc import Mapping
from functools import partial
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from sales_analytics import audit
from sales_analytics.db import (
    PUBLICATION_LOCK as PUBLICATION_LOCK,
)
from sales_analytics.db import (
    acquire_publication_lock,
    connect_database,
)
from sales_analytics.extract import SOURCES, SourceError
from sales_analytics.load_checks import (
    LoadBlocked,
    reconcile,
    same_snapshot,
    source_controls,
    verify_source_evidence,
)
from sales_analytics.recovery import confirmed_publication
from sales_analytics.transform import COLUMNS, TRANSFORM_VERSION, transform_records
from sales_analytics.validation import (
    KEYS,
    add_issue,
    convert_records,
    validate_records,
)
from sales_analytics.validation_run import _check_extraction, read_staging


class PublicationFailed(RuntimeError):
    """La publicación no fue confirmada; el mensaje identifica su diagnóstico."""


def _upsert_rows(conn, table: str, columns: tuple, key_count: int, rows) -> None:
    keys = columns[:key_count]
    values = columns[key_count:]
    compare = tuple(c for c in values if c != "run_id")

    def identifiers(names):
        return sql.SQL(",").join(map(sql.Identifier, names))

    statement = sql.SQL("INSERT INTO dw.{} ({}) VALUES ({}) ON CONFLICT ({}) ").format(
        sql.Identifier(table),
        identifiers(columns),
        sql.SQL(",").join(sql.Placeholder() for _ in columns),
        identifiers(keys),
    )
    if values:
        statement += sql.SQL(
            "DO UPDATE SET ({})=ROW({}) WHERE ({}) IS DISTINCT FROM ({})"
        ).format(
            identifiers(values),
            sql.SQL(",").join(sql.Identifier("excluded", c) for c in values),
            sql.SQL(",").join(sql.Identifier(table, c) for c in compare),
            sql.SQL(",").join(sql.Identifier("excluded", c) for c in compare),
        )
    else:
        statement += sql.SQL("DO NOTHING")
    with conn.cursor() as cursor:
        cursor.executemany(statement, rows)


def _write_dimensions(conn, candidate: dict) -> None:
    for table in ("dim_fecha", "dim_cliente", "dim_producto", "dim_sucursal"):
        _upsert_rows(conn, table, COLUMNS[table], 1, candidate[table])


def _write_rates(conn, candidate: dict) -> None:
    _upsert_rows(
        conn, "tipos_cambio", COLUMNS["tipos_cambio"], 2, candidate["tipos_cambio"]
    )


def _write_facts(conn, run_id: UUID, candidate: dict) -> None:
    maps = []
    for table, natural, surrogate in (
        ("dim_cliente", "customer_key", "cliente_id"),
        ("dim_producto", "product_key", "producto_id"),
        ("dim_sucursal", "store_key", "sucursal_id"),
    ):
        maps.append(
            dict(
                conn.execute(
                    sql.SQL("SELECT {},{} FROM dw.{}").format(
                        sql.Identifier(natural),
                        sql.Identifier(surrogate),
                        sql.Identifier(table),
                    )
                ).fetchall()
            )
        )
    rows = (
        (*r[:4], maps[0][r[4]], maps[1][r[5]], maps[2][r[6]], *r[7:], run_id)
        for r in candidate["fact_ventas"]
    )
    _upsert_rows(conn, "fact_ventas", (*COLUMNS["fact_ventas"], "run_id"), 2, rows)


def _write_candidate(conn, run_id: UUID, candidate: dict) -> None:
    _write_dimensions(conn, candidate)
    _write_rates(conn, candidate)
    _write_facts(conn, run_id, candidate)


def _check_validated(conn, attempt: dict, records: dict, previous: dict | None) -> None:
    if (
        previous
        and previous["conteos"].get("publicacion", {}).get("version_transformacion")
        != TRANSFORM_VERSION
    ):
        raise LoadBlocked("C8", "Versión de transformación publicada desconocida.")
    summary = attempt["conteos"].get("validacion", {})
    if (
        summary.get("version_validacion") != "calidad-v1"
        or summary.get("apto") is not True
        or summary.get("criticos") != 0
        or summary.get("rechazados") != 0
    ):
        raise LoadBlocked(
            "C8", "Falta validación E4 satisfactoria con versión conocida."
        )
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM ops.incidencias WHERE run_id=%s "
        "AND clasificacion<>'advertencia')",
        (attempt["run_id"],),
    ).fetchone()[0]:
        raise LoadBlocked("C8", "El intento conserva incidencias bloqueantes.")
    checks = []
    _check_extraction(attempt, records, partial(add_issue, checks))
    if checks:
        raise LoadBlocked(checks[0]["regla"], checks[0]["motivo"])
    verify_source_evidence(attempt, records)
    contract = summary["contrato_revisado"]
    authorizations = {
        a["cambio_id"]: a["autorizacion"] for a in summary["autorizaciones_aplicadas"]
    }
    prior = read_staging(conn, previous["run_id"]) if previous else None
    if prior:
        current_typed = convert_records(records, [])
        prior_typed = convert_records(prior, [])
        for name, keys in KEYS.items():
            old = {tuple(r[k] for k in keys) for r in prior_typed[name]}
            new = {tuple(r[k] for k in keys) for r in current_typed[name]}
            if old - new:
                raise LoadBlocked(
                    "C6", "Snapshot omite claves previas; no se borra histórico."
                )
    result = validate_records(
        records,
        previous=prior,
        authorizations=authorizations,
        reviewed_currencies=set(contract["monedas"]),
        reviewed_physical_stores=set(contract["sucursales_fisicas"]),
    )
    if not result["apto"]:
        rule = next(
            i["regla"]
            for i in result["incidencias"]
            if i["clasificacion"] != "advertencia"
        )
        raise LoadBlocked(
            rule, "El lote no supera los controles bajo exclusión de publicación."
        )


def publish_run(
    run_id: UUID,
    *,
    environ: Mapping[str, str] | None = None,
    diagnostics_dir: Path = Path(".local/publication-errors"),
) -> dict:
    """Publica un intento local validado. Nunca crea ni copia su historial."""
    owns_attempt = False
    committing = False
    try:
        with connect_database("writer", environ=environ) as claim:
            owns_attempt = audit.claim_phase(
                claim, run_id, "validacion_completa", "publicacion"
            )
            committing = True
        committing = False
        with connect_database("writer", environ=environ) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            conn.execute("SET LOCAL lock_timeout='2s'")
            acquire_publication_lock(conn)
            with conn.cursor(row_factory=dict_row) as cursor:
                attempt = cursor.execute(
                    "SELECT * FROM ops.ejecuciones WHERE run_id=%s FOR UPDATE NOWAIT",
                    (run_id,),
                ).fetchone()
                previous = cursor.execute(
                    "SELECT * FROM ops.ejecuciones WHERE publication_id IS NOT NULL "
                    "ORDER BY publication_id DESC LIMIT 1"
                ).fetchone()
            if attempt is None or not (
                (attempt["estado"] == "en_curso" and attempt["fase"] == "publicacion")
                or attempt["estado"] in ("publicado", "sin_cambios")
            ):
                raise ValueError(
                    "El intento no existe en esta base o no está validado."
                )
            owns_attempt = attempt["estado"] == "en_curso"
            if not owns_attempt:
                return {
                    "run_id": str(run_id),
                    "estado": attempt["estado"],
                    "publication_id": attempt["publication_id"],
                    "publication_id_vigente": previous["publication_id"]
                    if previous
                    else None,
                    "conciliacion": attempt["conteos"]["publicacion"]["conciliacion"],
                }
            for table, _, _ in SOURCES.values():
                if table:
                    conn.execute(
                        sql.SQL("LOCK TABLE staging.{} IN SHARE MODE").format(
                            sql.Identifier(table)
                        )
                    )
            for table in COLUMNS:
                conn.execute(
                    sql.SQL("LOCK TABLE dw.{} IN SHARE ROW EXCLUSIVE MODE").format(
                        sql.Identifier(table)
                    )
                )
            records = read_staging(conn, run_id)
            _check_validated(conn, attempt, records, previous)
            candidate = transform_records(records)
            expected = source_controls(records)
            if previous:
                prior = read_staging(conn, previous["run_id"])
                old_candidate = transform_records(prior)
                reconcile(conn, old_candidate, source_controls(prior))
                unchanged = same_snapshot(attempt, previous) or all(
                    set(candidate[t]) == set(old_candidate[t]) for t in COLUMNS
                )
            else:
                unchanged = False
                for table in COLUMNS:
                    if conn.execute(
                        sql.SQL("SELECT EXISTS(SELECT 1 FROM dw.{})").format(
                            sql.Identifier(table)
                        )
                    ).fetchone()[0]:
                        raise LoadBlocked(
                            "C5", "El DW contiene filas sin publicación confirmada."
                        )
            if unchanged:
                totals = reconcile(conn, candidate, expected)
                status, publication = "sin_cambios", None
            else:
                _write_candidate(conn, run_id, candidate)
                totals = reconcile(conn, candidate, expected)
                publication = previous["publication_id"] + 1 if previous else 1
                status = "publicado"
            audit.record_publication(
                conn, run_id, status, publication, totals, candidate
            )
            result = {
                "run_id": str(run_id),
                "estado": status,
                "publication_id": publication,
                "publication_id_vigente": publication or previous["publication_id"],
                "conciliacion": totals,
            }
            committing = True
        return result
    except (LoadBlocked, SourceError, psycopg.Error, ConnectionError, OSError) as error:
        concurrent = isinstance(
            error,
            (
                psycopg.errors.LockNotAvailable,
                psycopg.errors.SerializationFailure,
                psycopg.errors.DeadlockDetected,
            ),
        )
        uncertain = committing and not concurrent
        if uncertain:
            try:
                confirmed = confirmed_publication(run_id, environ=environ)
                if confirmed is not None:
                    return confirmed
            except (psycopg.Error, ConnectionError, OSError):
                pass  # El diagnóstico local conserva la incertidumbre explícita.
        diagnostic = {
            "regla": getattr(
                error, "rule", "C1" if isinstance(error, SourceError) else "C5"
            ),
            "motivo": str(error)
            if isinstance(error, (LoadBlocked, SourceError))
            else f"Fallo operativo de publicación ({type(error).__name__}).",
            "ordinal": None,
            "incierto": uncertain,
        }
        try:
            if not owns_attempt or uncertain:
                # Sin respuesta al commit no se infiere rollback ni se reclasifica.
                location = audit.write_local_diagnostic(
                    run_id, dict(diagnostic, run_id=str(run_id)), diagnostics_dir
                )
            else:
                location = audit.record_failure(
                    run_id,
                    None,
                    {},
                    diagnostic,
                    environ,
                    diagnostics_dir,
                    phase="publicacion_fallida",
                    expected_phase="publicacion",
                )
        except OSError:
            location = "no persistido: base y disco no disponibles"
        raise PublicationFailed(
            f"Publicación no confirmada: run_id={run_id}; diagnóstico: {location}"
        ) from None
