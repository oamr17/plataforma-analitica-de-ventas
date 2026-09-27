"""Auditoría de ejecuciones ETL, incidencias e identidad de publicación."""

import ctypes
import json
import os
import platform
from collections.abc import Mapping
from ctypes import wintypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from sales_analytics.db import acquire_publication_lock, connect_database
from sales_analytics.transform import TRANSFORM_VERSION


class CommitNotConfirmed(ConnectionError):
    """Se intentó confirmar el inicio, pero se perdió su respuesta."""


def process_started_at(pid: int | None = None) -> datetime:
    """Inicio real del proceso Windows: permite distinguir un PID reutilizado."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    times = [wintypes.FILETIME() for _ in range(4)]
    handle = (
        kernel.GetCurrentProcess()
        if pid is None
        else kernel.OpenProcess(0x101000, False, pid)
    )
    if not handle:
        if ctypes.get_last_error() == 87:
            raise ProcessLookupError("El PID ya no existe.")
        raise PermissionError("No se pudo inspeccionar el proceso.")
    try:
        if pid is not None and kernel.WaitForSingleObject(handle, 0) == 0:
            raise ProcessLookupError("El proceso terminó.")
        if not kernel.GetProcessTimes(
            handle, *(ctypes.byref(value) for value in times)
        ):
            raise OSError("No se pudo identificar el inicio del proceso Windows.")
    finally:
        if pid is not None:
            kernel.CloseHandle(handle)
    ticks = (times[0].dwHighDateTime << 32) + times[0].dwLowDateTime
    return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)


def record_reconciliation(
    conn: psycopg.Connection, run_id: UUID, status: str, evidence: dict
) -> None:
    conn.execute(
        "UPDATE ops.ejecuciones SET estado=%s,fase='reconciliado',"
        "fin=clock_timestamp(),ultimo_avance=clock_timestamp() WHERE run_id=%s",
        (status, run_id),
    )

    conn.execute(
        "INSERT INTO ops.incidencias(run_id,regla,clasificacion,motivo,evidencia) "
        "VALUES (%s,'reconciliacion_manual','advertencia',%s,%s)",
        (
            run_id,
            "Propietario terminado y ausencia de publicación comprobados.",
            json.dumps(evidence, ensure_ascii=True),
        ),
    )


def claim_phase(
    conn: psycopg.Connection, run_id: UUID, expected: str, phase: str
) -> bool:
    """Entrega explícita de una fase completa; confirma identidad antes del trabajo."""
    acquire_publication_lock(conn)
    row = conn.execute(
        "SELECT estado,fase FROM ops.ejecuciones WHERE run_id=%s FOR UPDATE NOWAIT",
        (run_id,),
    ).fetchone()
    if row and row[0] in ("publicado", "sin_cambios") and phase == "publicacion":
        return False
    if row != ("en_curso", expected):
        raise ValueError("El intento no está en la fase disponible para continuar.")
    conn.execute(
        "UPDATE ops.ejecuciones SET conteos=conteos || jsonb_build_object("
        "'proceso_anterior',jsonb_build_object('equipo',equipo,'pid',pid,"
        "'inicio_proceso',inicio_proceso)),fase=%s,equipo=%s,pid=%s,"
        "inicio_proceso=%s,ultimo_avance=clock_timestamp() WHERE run_id=%s",
        (phase, platform.node(), os.getpid(), process_started_at(), run_id),
    )
    return True


def start_run(run_id: UUID, manifest: dict, environ: Mapping[str, str] | None) -> None:
    committing = False
    try:
        with connect_database("writer", environ=environ) as conn:
            conn.execute(
                "INSERT INTO ops.ejecuciones "
                "(run_id, version_reglas, archivos, equipo, pid, inicio_proceso, fase) "
                "VALUES (%s, 'estructura-csv-v1', %s, %s, %s, %s, 'extraccion')",
                (
                    run_id,
                    Jsonb(manifest),
                    platform.node(),
                    os.getpid(),
                    process_started_at(),
                ),
            )
            committing = True
    except psycopg.OperationalError:
        if committing:
            raise CommitNotConfirmed("Confirmación del inicio no recibida.") from None
        raise


def record_file(
    conn: psycopg.Connection, run_id: UUID, name: str, detail: dict
) -> None:
    """El archivo confirmado y sus metadatos usan la misma transacción."""
    counts = {name: detail["registros"]} if detail["estado"] == "confirmado" else {}
    conn.execute(
        "UPDATE ops.ejecuciones SET archivos=archivos || %s, conteos=conteos || %s, "
        "fase='extraccion', ultimo_avance=clock_timestamp() WHERE run_id=%s",
        (Jsonb({name: detail}), Jsonb(counts), run_id),
    )


def record_failure(
    run_id: UUID,
    name: str | None,
    detail: dict,
    diagnostic: dict,
    environ: Mapping[str, str] | None,
    diagnostics_dir: Path,
    *,
    phase: str = "extraccion_fallida",
    expected_phase: str | None = None,
) -> str:
    """Si ops no está disponible, deja evidencia local; nunca registra el DSN."""
    evidence = dict(diagnostic, run_id=str(run_id), archivo=name, origen=detail)
    try:
        with connect_database("writer", environ=environ) as conn:
            if diagnostic["incierto"]:
                # Puede haberse confirmado el archivo; no sobrescribir su manifiesto.
                updated = conn.execute(
                    "UPDATE ops.ejecuciones SET fase='commit_no_confirmado', "
                    "ultimo_avance=clock_timestamp() WHERE run_id=%s "
                    "AND estado='en_curso' AND publication_id IS NULL "
                    "AND fase=%s",
                    (run_id, expected_phase or "extraccion"),
                )
            else:
                updated = conn.execute(
                    "UPDATE ops.ejecuciones SET estado='fallido', "
                    "fase=%s, "
                    "archivos=archivos || %s, fin=clock_timestamp(), "
                    "ultimo_avance=clock_timestamp() WHERE run_id=%s "
                    "AND estado='en_curso' AND publication_id IS NULL "
                    "AND (%s::text IS NULL OR (fase=%s AND estado='en_curso'))",
                    (
                        phase,
                        Jsonb({name: detail} if name else {}),
                        run_id,
                        expected_phase,
                        expected_phase,
                    ),
                )
            if updated.rowcount == 0:
                return write_local_diagnostic(run_id, evidence, diagnostics_dir)
            conn.execute(
                "INSERT INTO ops.incidencias "
                "(run_id, archivo, numero_registro_origen, regla, "
                "clasificacion, motivo, evidencia) "
                "VALUES (%s, %s, %s, %s, 'error_critico', %s, %s)",
                (
                    run_id,
                    name,
                    diagnostic["ordinal"],
                    diagnostic["regla"],
                    diagnostic["motivo"],
                    json.dumps(evidence, ensure_ascii=True),
                ),
            )
        return "ops.incidencias"
    except (psycopg.Error, ConnectionError):
        return write_local_diagnostic(run_id, evidence, diagnostics_dir)


def write_local_diagnostic(run_id: UUID, evidence: dict, directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{run_id}.json"
    try:
        stream = target.open("x", encoding="utf-8")
    except FileExistsError:
        target = directory / f"{run_id}-{uuid4().hex}.json"
        stream = target.open("x", encoding="utf-8")
    with stream:
        json.dump(evidence, stream, ensure_ascii=True, indent=2)
    return str(target)


def record_validation(conn: psycopg.Connection, run_id: UUID, result: dict) -> None:
    """Resumen e incidencias se confirman juntos; no toca archivos ni staging."""
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY ops.incidencias (run_id,archivo,numero_registro_origen,regla,"
            "clasificacion,motivo,evidencia) FROM STDIN"
        ) as copy,
    ):
        for item in result["incidencias"]:
            copy.write_row(
                (
                    run_id,
                    item["archivo"],
                    item["numero_registro_origen"],
                    item["regla"],
                    item["clasificacion"],
                    item["motivo"],
                    json.dumps(item["evidencia"], ensure_ascii=True),
                )
            )
    summary = {key: value for key, value in result.items() if key != "incidencias"}
    conn.execute(
        "UPDATE ops.ejecuciones SET conteos=conteos || %s, estado=%s, fase=%s, "
        "fin=CASE WHEN %s THEN NULL ELSE clock_timestamp() END, "
        "ultimo_avance=clock_timestamp(), equipo=%s, pid=%s, inicio_proceso=%s "
        "WHERE run_id=%s",
        (
            Jsonb({"validacion": summary}),
            "en_curso" if result["apto"] else "fallido",
            "validacion_completa" if result["apto"] else "validacion_bloqueada",
            result["apto"],
            platform.node(),
            os.getpid(),
            process_started_at(),
            run_id,
        ),
    )


def record_publication(
    conn: psycopg.Connection,
    run_id: UUID,
    status: str,
    publication_id: int | None,
    totals: dict,
    candidate: dict,
) -> None:
    """Metadatos confirmados exclusivamente en la transacción de los datos DW."""
    dates = [r[2] for r in candidate["fact_ventas"]]
    conn.execute(
        "UPDATE ops.ejecuciones SET estado=%s,publication_id=%s,fase=%s,"
        "conteos=conteos || %s,fecha_pedido_min=%s,fecha_pedido_max=%s,"
        "fin=clock_timestamp(),ultimo_avance=clock_timestamp(),equipo=%s,pid=%s,"
        "inicio_proceso=%s WHERE run_id=%s",
        (
            status,
            publication_id,
            "publicacion_completa" if status == "publicado" else "sin_cambios",
            Jsonb(
                {
                    "publicacion": {
                        "version_transformacion": TRANSFORM_VERSION,
                        "conciliacion": totals,
                    }
                }
            ),
            min(dates),
            max(dates),
            platform.node(),
            os.getpid(),
            process_started_at(),
            run_id,
        ),
    )
