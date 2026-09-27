"""Protocolo manual: identifica al propietario y modifica únicamente auditoría."""

import hashlib
import json
import platform
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from sales_analytics import audit
from sales_analytics.db import acquire_publication_lock, connect_database


def process_status(host: str, pid: int, started: datetime) -> str:
    if host.casefold() != platform.node().casefold():
        return "desconocido"
    try:
        actual = audit.process_started_at(pid)
    except ProcessLookupError:
        return "terminado"
    except OSError:
        return "desconocido"
    return "activo" if actual == started else "pid_reutilizado"


def _read_attempt(conn, run_id, *, lock=False):
    with conn.cursor(row_factory=dict_row) as cursor:
        statement = "SELECT * FROM ops.ejecuciones WHERE run_id=%s"
        if lock:
            statement += " FOR UPDATE NOWAIT"
        attempt = cursor.execute(statement, (run_id,)).fetchone()
    if attempt is None:
        raise ValueError("El intento no existe en esta base.")
    return attempt


def confirmed_publication(run_id: UUID, *, environ=None) -> dict | None:
    """Resuelve una respuesta perdida leyendo el resultado, sin repetir escrituras."""
    with connect_database("writer", environ=environ) as conn:
        acquire_publication_lock(conn)
        attempt = _read_attempt(conn, run_id)
        if attempt["estado"] not in ("publicado", "sin_cambios"):
            return None
        current = conn.execute(
            "SELECT max(publication_id) FROM ops.ejecuciones"
        ).fetchone()[0]
        return {
            "run_id": str(run_id),
            "estado": attempt["estado"],
            "publication_id": attempt["publication_id"],
            "publication_id_vigente": current,
            "conciliacion": attempt["conteos"]["publicacion"]["conciliacion"],
        }


def reconcile_run(
    run_id: UUID,
    *,
    environ: Mapping[str, str] | None = None,
    diagnostic_path: Path | None = None,
) -> dict:
    evidence = {}
    if diagnostic_path is not None:
        raw = diagnostic_path.read_bytes()
        diagnostic = json.loads(raw)
        if diagnostic.get("run_id") != str(run_id) or not isinstance(
            diagnostic.get("incierto"), bool
        ):
            raise ValueError(
                "Diagnóstico ajeno al intento o sin resultado identificable."
            )
        evidence = {
            "diagnostico_sha256": hashlib.sha256(raw).hexdigest(),
            "commit_incierto": diagnostic["incierto"],
        }
    with connect_database("writer", environ=environ) as conn:
        conn.read_only = True
        before = _read_attempt(conn, run_id)
        observed_process = process_status(
            before["equipo"], before["pid"], before["inicio_proceso"]
        )
    try:
        with connect_database("writer", environ=environ) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            acquire_publication_lock(conn)
            attempt = _read_attempt(conn, run_id, lock=True)
            result = {
                "run_id": str(run_id),
                "estado": attempt["estado"],
                "publication_id": attempt["publication_id"],
            }
            if attempt["estado"] != "en_curso":
                return result
            process = process_status(
                attempt["equipo"], attempt["pid"], attempt["inicio_proceso"]
            )
            if process not in ("terminado", "pid_reutilizado"):
                return dict(result, pendiente=True, motivo=process)
            observed = (
                bool(evidence)
                or conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM ops.incidencias WHERE run_id=%s "
                    "AND clasificacion='error_critico')",
                    (run_id,),
                ).fetchone()[0]
            )
            result["estado"] = "fallido" if observed else "interrumpido"
            audit.record_reconciliation(
                conn,
                run_id,
                result["estado"],
                dict(
                    evidence,
                    proceso=process,
                    proceso_antes=observed_process,
                    fallo_documentado=observed,
                ),
            )
        return result
    except psycopg.errors.LockNotAvailable:
        return {
            "run_id": str(run_id),
            "estado": before["estado"],
            "publication_id": before["publication_id"],
            "pendiente": True,
            "motivo": "bloqueo_ocupado",
        }
