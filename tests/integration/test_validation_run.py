"""Continuidad, auditoría atómica y conservación en sales_analytics_test."""

from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql

from sales_analytics import audit, validation_run
from sales_analytics.db import connect_database


def snapshots(conn, run):
    result = {}
    for schema, table in conn.execute(
        "SELECT schemaname,tablename FROM pg_tables "
        "WHERE schemaname IN ('staging','dw','marts')"
    ):
        query = sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(schema), sql.Identifier(table)
        )
        if schema == "staging":
            query += sql.SQL(" WHERE run_id=%s ORDER BY numero_registro_origen")
        result[schema, table] = conn.execute(
            query, (run,) if schema == "staging" else ()
        ).fetchall()
    return result


def test_continue_same_run_and_keep_sources(staged_run):
    run, env, _ = staged_run
    with connect_database("writer", environ=env) as conn:
        before = snapshots(conn, run)
    result = validation_run.validate_run(run, environ=env)
    assert result["apto"] and result["aceptados"] == 5 and result["rechazados"] == 0
    with connect_database("writer", environ=env) as conn:
        assert snapshots(conn, run) == before
        state, phase, pub, counts = conn.execute(
            "SELECT estado,fase,publication_id,conteos "
            "FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone()
        assert (state, phase, pub) == ("en_curso", "validacion_completa", None)
        assert counts["validacion"]["apto"] is True
        assert counts["Sales.csv"] == 1
        issues = conn.execute(
            "SELECT archivo,numero_registro_origen,regla,clasificacion,evidencia "
            "FROM ops.incidencias WHERE run_id=%s",
            (run,),
        ).fetchall()
        assert any(i[:4] == ("Stores.csv", 1, "W1", "advertencia") for i in issues)
        assert "Persona sintética" not in str(issues) and "1990" not in str(issues)
    with pytest.raises(ValueError, match="fase"):
        validation_run.validate_run(run, environ=env)


@pytest.mark.parametrize(
    "defect,rule",
    [
        ("quantity", "R3"),
        ("missing_file", "C1"),
        ("manifest", "C8"),
        ("count", "C4"),
        ("version", "C8"),
        ("empty_sales", "C8"),
        ("header", "C1"),
    ],
)
def test_gate_blocks_invalid_batch(staged_run, defect, rule):
    run, env, path = staged_run
    with connect_database("writer", environ=env) as conn:
        if defect == "quantity":
            conn.execute(
                "UPDATE staging.sales SET \"Quantity\"='bad' WHERE run_id=%s", (run,)
            )
        elif defect == "missing_file":
            (path / "Sales.csv").unlink()
        elif defect == "header":
            (path / "Sales.csv").write_text("Unexpected\n", encoding="utf-8")
        elif defect == "manifest":
            conn.execute(
                "UPDATE ops.ejecuciones SET archivos=archivos-'Sales.csv' "
                "WHERE run_id=%s",
                (run,),
            )
        elif defect == "version":
            conn.execute(
                "UPDATE ops.ejecuciones SET version_reglas='unknown' WHERE run_id=%s",
                (run,),
            )
        elif defect == "count":
            conn.execute(
                "UPDATE ops.ejecuciones "
                "SET conteos=jsonb_set(conteos,'{Sales.csv}','2') "
                "WHERE run_id=%s",
                (run,),
            )
        else:
            conn.execute("DELETE FROM staging.sales WHERE run_id=%s", (run,))
        before = snapshots(conn, run)
    result = validation_run.validate_run(run, environ=env)
    assert result["apto"] is False and rule in result["por_regla"]
    assert result["entradas"] == result["aceptados"] + result["rechazados"]
    with connect_database("writer", environ=env) as conn:
        assert snapshots(conn, run) == before
        assert conn.execute(
            "SELECT estado,fase,publication_id FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone() == ("fallido", "validacion_bloqueada", None)


def test_failed_audit_rolls_back_all_validation_results(staged_run, monkeypatch):
    run, env, path = staged_run
    original = audit.record_validation

    def failing(conn, *args):
        original(conn, *args)
        raise psycopg.OperationalError("fixture-password-must-not-appear")

    monkeypatch.setattr(audit, "record_validation", failing)
    with pytest.raises(validation_run.ValidationFailed) as caught:
        validation_run.validate_run(
            run, environ=env, diagnostics_dir=path / "diagnostics"
        )
    assert "fixture-password" not in str(caught.value)
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT regla FROM ops.incidencias WHERE run_id=%s", (run,)
        ).fetchall() == [("C5",)]
        assert conn.execute(
            "SELECT fase FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ("validacion_fallida",)


def test_same_run_concurrency_does_not_damage_active_attempt(staged_run):
    run, env, path = staged_run
    with connect_database("writer", environ=env) as owner:
        owner.execute(
            "SELECT run_id FROM ops.ejecuciones WHERE run_id=%s FOR UPDATE", (run,)
        )
        with pytest.raises(validation_run.ValidationFailed):
            validation_run.validate_run(
                run, environ=env, diagnostics_dir=path / "diagnostics"
            )
        assert owner.execute(
            "SELECT estado,fase FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ("en_curso", "extraccion_completa")
    assert (path / "diagnostics" / f"{run}.json").exists()


def test_uncertain_validation_commit_preserves_confirmed_results(
    staged_run, monkeypatch
):
    run, env, path = staged_run
    original = validation_run.connect_database

    @contextmanager
    def lost_reply(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield conn
            complete = conn.execute(
                "SELECT fase='validacion_completa' FROM ops.ejecuciones WHERE "
                "run_id=%s",
                (run,),
            ).fetchone()[0]
        if complete:
            raise psycopg.OperationalError("fixture: reply lost")

    monkeypatch.setattr(validation_run, "connect_database", lost_reply)
    with pytest.raises(validation_run.ValidationFailed):
        validation_run.validate_run(
            run, environ=env, diagnostics_dir=path / "diagnostics"
        )
    with connect_database("writer", environ=env) as conn:
        state, phase, counts = conn.execute(
            "SELECT estado,fase,conteos FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone()
        assert (state, phase) == ("en_curso", "validacion_completa")
        assert counts["validacion"]["apto"] is True
    assert (path / "diagnostics" / f"{run}.json").exists()


@pytest.mark.parametrize(
    "failure", [psycopg.OperationalError, psycopg.errors.SerializationFailure]
)
def test_failure_without_ownership_preserves_completed_validation(
    staged_run, monkeypatch, failure
):
    run, env, path = staged_run
    validation_run.validate_run(run, environ=env)

    @contextmanager
    def unavailable(*args, **kwargs):
        raise failure("fixture: connection or snapshot conflict")
        yield

    monkeypatch.setattr(validation_run, "connect_database", unavailable)
    with pytest.raises(validation_run.ValidationFailed):
        validation_run.validate_run(
            run, environ=env, diagnostics_dir=path / "diagnostics"
        )
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT estado,fase FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ("en_curso", "validacion_completa")
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND regla='C5'",
            (run,),
        ).fetchone() == (0,)
    assert (path / "diagnostics" / f"{run}.json").exists()


def test_late_failure_cannot_overwrite_completed_validation(staged_run):
    run, env, path = staged_run
    validation_run.validate_run(run, environ=env)
    location = audit.record_failure(
        run,
        None,
        {},
        {"regla": "C5", "motivo": "Fallo simulado", "ordinal": None, "incierto": False},
        env,
        path / "diagnostics",
        phase="validacion_fallida",
        expected_phase="extraccion_completa",
    )
    assert location != "ops.incidencias"
    with connect_database("writer", environ=env) as conn:
        assert conn.execute(
            "SELECT estado,fase FROM ops.ejecuciones WHERE run_id=%s", (run,)
        ).fetchone() == ("en_curso", "validacion_completa")
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s AND regla='C5'",
            (run,),
        ).fetchone() == (0,)
