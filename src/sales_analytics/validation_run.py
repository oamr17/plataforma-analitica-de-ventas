"""Validación de un intento extraído: lectura consistente y auditoría atómica."""

import hashlib
import re
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from sales_analytics import audit
from sales_analytics.db import connect_database
from sales_analytics.extract import SOURCES, SourceError, read_snapshot
from sales_analytics.validation import (
    INITIAL_CURRENCIES,
    INITIAL_PHYSICAL_STORES,
    add_issue,
    validate_records,
)


class ValidationFailed(RuntimeError):
    """Fallo operativo, distinto de un lote que terminó validación con rechazos."""


def read_staging(conn: psycopg.Connection, run_id: UUID) -> dict:
    records = {}
    with conn.cursor(row_factory=dict_row) as cursor:
        for name, (table, headers, _) in SOURCES.items():
            if table:
                query = sql.SQL(
                    "SELECT numero_registro_origen, {} FROM staging.{} "
                    "WHERE run_id=%s ORDER BY numero_registro_origen"
                ).format(
                    sql.SQL(",").join(map(sql.Identifier, headers)),
                    sql.Identifier(table),
                )
                records[name] = cursor.execute(query, (run_id,)).fetchall()
    return records


def _check_extraction(attempt: dict, records: dict, add) -> None:
    manifest = attempt["archivos"]
    if set(manifest) != set(SOURCES) or attempt["modo"] != "snapshot_completo":
        add(
            None, None, "C8", "Manifiesto diferente del conjunto de archivos requerido."
        )
    if attempt["version_reglas"] != "estructura-csv-v1":
        add(None, None, "C8", "Versión de contrato de extracción desconocida.")
    for name in SOURCES:
        detail = manifest.get(name, {})
        if detail.get("estado") != "confirmado":
            add(name, None, "C1", "Lectura estructural sin confirmación de E3.")
        digest = detail.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch("[a-fA-F0-9]{64}", digest):
            add(name, None, "C8", "Falta identidad SHA-256 del archivo confirmado.")
        try:
            raw, _ = read_snapshot(Path(detail.get("ruta", "")))
            if hashlib.sha256(raw).hexdigest() != digest:
                add(
                    name,
                    None,
                    "C1",
                    "El origen no coincide con los bytes verificados en E3.",
                )
        except SourceError:
            add(
                name,
                None,
                "C1",
                "El archivo original no está disponible para verificar su identidad.",
            )
        if name in records:
            rows = records[name]
            count = len(rows)
            if (
                detail.get("registros") != count
                or attempt["conteos"].get(name) != count
                or any(r["numero_registro_origen"] != i for i, r in enumerate(rows, 1))
            ):
                add(
                    name,
                    None,
                    "C4",
                    "Conteos u ordinales de staging no concilian con E3.",
                )
        elif detail.get("registros") != 37 or attempt["conteos"].get(name) != 37:
            add(
                name,
                None,
                "C8",
                "Diccionario incompleto para el contrato de 37 campos.",
            )
    if not records["Sales.csv"]:
        add(
            "Sales.csv",
            None,
            "C8",
            "Ventas vacías sin alcance explícitamente justificado.",
        )


def _previous_snapshot(conn: psycopg.Connection, add) -> tuple[dict | None, str | None]:
    previous = conn.execute(
        "SELECT run_id,archivos,conteos,modo,version_reglas "
        "FROM ops.ejecuciones WHERE publication_id IS NOT NULL "
        "ORDER BY publication_id DESC LIMIT 1"
    ).fetchone()
    if previous is None:
        for table in (
            "dim_cliente",
            "dim_producto",
            "dim_sucursal",
            "tipos_cambio",
            "fact_ventas",
        ):
            if conn.execute(
                sql.SQL("SELECT EXISTS(SELECT 1 FROM dw.{})").format(
                    sql.Identifier(table)
                )
            ).fetchone()[0]:
                add(
                    None,
                    None,
                    "C5",
                    "Existen datos DW sin publicación confirmada de referencia.",
                )
                break
        return None, None
    run = previous[0]
    records = read_staging(conn, run)
    if any(len(rows) != previous[2].get(file) for file, rows in records.items()):
        add(None, None, "C6", "La evidencia de la publicación previa está incompleta.")
    return records, str(run)


def validate_run(
    run_id: UUID,
    *,
    environ: Mapping[str, str] | None = None,
    authorizations: Mapping[str, str] | None = None,
    reviewed_currencies: set[str] | frozenset[str] = INITIAL_CURRENCIES,
    reviewed_physical_stores: set[int] | frozenset[int] = INITIAL_PHYSICAL_STORES,
    diagnostics_dir: Path = Path(".local/validation-errors"),
) -> dict:
    """Continúa únicamente extraccion_completa; no reconcilia intentos interrumpidos."""
    committing = False
    owns_attempt = False
    try:
        with connect_database("writer", environ=environ) as claim:
            owns_attempt = audit.claim_phase(
                claim, run_id, "extraccion_completa", "validacion"
            )
            committing = True
        committing = False
        with connect_database("writer", environ=environ) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SET LOCAL lock_timeout='2s'")
            with conn.cursor(row_factory=dict_row) as cursor:
                attempt = cursor.execute(
                    "SELECT * FROM ops.ejecuciones WHERE run_id=%s FOR UPDATE NOWAIT",
                    (run_id,),
                ).fetchone()
            if (
                attempt is None
                or attempt["estado"] != "en_curso"
                or attempt["fase"] != "validacion"
                or attempt["publication_id"] is not None
            ):
                raise ValueError(
                    "El intento no está en la fase extraccion_completa disponible."
                )
            owns_attempt = True
            for table, _, _ in SOURCES.values():
                if table:
                    conn.execute(
                        sql.SQL("LOCK TABLE staging.{} IN SHARE MODE").format(
                            sql.Identifier(table)
                        )
                    )
            records = read_staging(conn, run_id)
            checks = []
            add = partial(add_issue, checks)
            _check_extraction(attempt, records, add)
            previous, base_run = _previous_snapshot(conn, add)
            result = validate_records(
                records,
                previous=previous,
                authorizations=authorizations,
                reviewed_currencies=reviewed_currencies,
                reviewed_physical_stores=reviewed_physical_stores,
            )
            result["incidencias"].extend(checks)
            for item in checks:
                result["por_regla"][item["regla"]] = (
                    result["por_regla"].get(item["regla"], 0) + 1
                )
            result["criticos"] += len(checks)
            result["apto"] = result["apto"] and not checks
            result["version_validacion"] = "calidad-v1"
            result["referencia_run_id"] = base_run
            result["proceso_extraccion"] = {
                k: str(attempt["conteos"]["proceso_anterior"][k])
                for k in ("equipo", "pid", "inicio_proceso")
            }
            result["pendiente_publicacion"] = [
                "conciliacion_DW",
                "conflictos_bajo_exclusion",
                "commit_atomico",
            ]
            audit.record_validation(conn, run_id, result)
            committing = True
        return result
    except (psycopg.Error, ConnectionError, OSError) as error:
        concurrency_error = isinstance(
            error,
            (
                psycopg.errors.LockNotAvailable,
                psycopg.errors.SerializationFailure,
                psycopg.errors.DeadlockDetected,
            ),
        )
        diagnostic = {
            "regla": "C5",
            "motivo": f"Fallo operativo de validación ({type(error).__name__}).",
            "ordinal": None,
            "incierto": committing and not concurrency_error,
        }
        try:
            if not owns_attempt or concurrency_error:
                # El propietario sigue trabajando; no reclasificar su intento.
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
                    phase="validacion_fallida",
                    expected_phase="validacion",
                )
        except OSError:
            location = "no persistido: base y disco no disponibles"
        raise ValidationFailed(
            f"Validación no confirmada: run_id={run_id}; diagnóstico: {location}"
        ) from None
