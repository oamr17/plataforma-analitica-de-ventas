"""Reconciliación manual sin modificar el DW; fixtures de la base E6."""

import json
import os
import platform
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import UUID

import pytest

from sales_analytics import load, recovery
from sales_analytics.db import connect_database


@pytest.fixture
def ended_process():
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from sales_analytics.audit import process_started_at; "
            "print(process_started_at().isoformat(),flush=True); input()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    started = datetime.fromisoformat(child.stdout.readline().strip())
    child.communicate("\n", timeout=10)
    assert child.returncode == 0
    return platform.node(), child.pid, started


@pytest.mark.parametrize("phase", ["extraccion", "validacion", "publicacion"])
def test_ended_process_reconciles_each_interrupted_phase(batch, ended_process, phase):
    run, env, _, _ = batch
    with connect_database("writer", environ=env) as conn:
        conn.execute(
            "UPDATE ops.ejecuciones SET equipo=%s,pid=%s,inicio_proceso=%s,fase=%s "
            "WHERE run_id=%s",
            (*ended_process, phase, run),
        )
    result = recovery.reconcile_run(run, environ=env)
    assert result["estado"] == "interrumpido" and result["publication_id"] is None
    assert recovery.reconcile_run(run, environ=env) == result
    with connect_database("writer", environ=env) as conn:
        assert conn.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND "
            "regla='reconciliacion_manual'",
            (run,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("condition", ["active", "reused", "remote", "lock"])
def test_reconciliation_guards_process_and_lock(batch, condition):
    run, env, _, _ = batch
    with connect_database("writer", environ=env) as conn:
        conn.execute(
            "UPDATE ops.ejecuciones SET ultimo_avance=inicio-interval '30 days' "
            "WHERE run_id=%s",
            (run,),
        )
        if condition == "reused":
            conn.execute(
                "UPDATE ops.ejecuciones SET inicio_proceso=inicio_proceso-interval "
                "'1 second' WHERE run_id=%s",
                (run,),
            )
        elif condition == "remote":
            conn.execute(
                "UPDATE ops.ejecuciones SET equipo='equipo-inaccesible-E6' WHERE "
                "run_id=%s",
                (run,),
            )
    with connect_database("writer", environ=env) as owner:
        if condition == "lock":
            owner.execute("SELECT pg_advisory_xact_lock(%s)", (load.PUBLICATION_LOCK,))
        result = recovery.reconcile_run(run, environ=env)
    assert result["estado"] == ("interrumpido" if condition == "reused" else "en_curso")
    if condition != "reused":
        assert result["pendiente"] is True


@pytest.mark.parametrize("uncertain", [False, True])
def test_local_confirmed_failure_is_audited_after_database_returns(
    batch, ended_process, uncertain
):
    run, env, path, _ = batch
    with connect_database("writer", environ=env) as conn:
        conn.execute(
            "UPDATE ops.ejecuciones SET equipo=%s,pid=%s,inicio_proceso=%s WHERE "
            "run_id=%s",
            (*ended_process, run),
        )
    evidence = path / "failure.json"
    evidence.write_text(
        json.dumps({"run_id": str(run), "incierto": uncertain, "regla": "C5"})
    )
    assert (
        recovery.reconcile_run(run, environ=env, diagnostic_path=evidence)["estado"]
        == "fallido"
    )


def test_confirmed_publication_is_preserved_even_if_process_is_active(batch):
    run, env, _, _ = batch
    load.publish_run(run, environ=env)
    result = recovery.reconcile_run(run, environ=env)
    assert result["estado"] == "publicado" and result["publication_id"] == 1


def test_manual_cli_reconciles_and_preserves_terminal_result(batch, ended_process):
    run, env, _, _ = batch
    with connect_database("writer", environ=env) as conn:
        conn.execute(
            "UPDATE ops.ejecuciones SET equipo=%s,pid=%s,inicio_proceso=%s "
            "WHERE run_id=%s",
            (*ended_process, run),
        )
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "sales_analytics.cli", "reconcile", str(run)],
            env=dict(os.environ, **env),
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {
            "run_id": str(run),
            "estado": "interrumpido",
            "publication_id": None,
        }
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s "
            "AND regla='reconciliacion_manual'",
            (run,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("phase", ["extraccion", "validacion", "publicacion"])
def test_real_worker_interruption_is_reconciled_without_partial_data(
    batch, next_batch, phase
):
    run, env, _, others = batch
    if phase == "validacion":
        run, _ = next_batch(validate=False)
    worker = """
import sys
from uuid import UUID
from sales_analytics import extract, validation_run, load
phase=sys.argv[1]
run=UUID(sys.argv[2])
def pause(identifier):
    print(str(identifier),flush=True)
    input()
if phase=='extraccion':
    original=extract._persist_records
    def halted(conn,run,*args):
        original(conn,run,*args)
        pause(run)
    extract._persist_records=halted
    extract.extract_sources()
elif phase=='validacion':
    original=validation_run.read_staging
    def halted(conn,run):
        records=original(conn,run)
        pause(run)
        return records
    validation_run.read_staging=halted
    validation_run.validate_run(run)
else:
    original=load._write_dimensions
    def halted(conn,candidate):
        original(conn,candidate)
        pause(run)
    load._write_dimensions=halted
    load.publish_run(run)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", worker, phase, str(run)],
        env=dict(os.environ, **env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            announced = pool.submit(child.stdout.readline).result(timeout=20)
        assert announced.strip(), "El trabajador no llegó a la barrera."
        run = UUID(announced.strip())
        if phase == "extraccion":
            others.append(run)
        assert recovery.reconcile_run(run, environ=env)["pendiente"] is True
    finally:
        child.kill()
        child.communicate(timeout=10)
    with connect_database("writer", environ=env) as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute(
            "SELECT run_id FROM ops.ejecuciones WHERE run_id=%s FOR UPDATE", (run,)
        )
    result = recovery.reconcile_run(run, environ=env)
    assert result["estado"] == "interrumpido" and result["publication_id"] is None
    with connect_database("writer", environ=env) as conn:
        assert conn.execute("SELECT count(*) FROM dw.fact_ventas").fetchone() == (0,)
