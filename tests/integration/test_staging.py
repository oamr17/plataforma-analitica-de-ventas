"""Transacciones de extracción únicamente en sales_analytics_test."""

import hashlib
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql

from sales_analytics import audit, extract
from sales_analytics.db import connect_database


@pytest.fixture
def extraction_env(urls, source_files):
    path, _ = source_files
    env = type(urls)(urls)
    env["SALES_DATA_DIR"] = str(path)
    yield env
    # Solo intentos de esta fixture; E3 confirma transacciones reales por archivo.
    with connect_database("writer", environ=urls) as conn:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT run_id FROM ops.ejecuciones "
                "WHERE archivos->'Customers.csv'->>'ruta'=%s",
                (str(path / "Customers.csv"),),
            )
        ]
        for table in ("customers", "products", "stores", "sales", "exchange_rates"):
            conn.execute(
                sql.SQL("DELETE FROM staging.{} WHERE run_id=ANY(%s)").format(
                    sql.Identifier(table)
                ),
                (ids,),
            )
        conn.execute("DELETE FROM ops.incidencias WHERE run_id=ANY(%s)", (ids,))
        conn.execute("DELETE FROM ops.ejecuciones WHERE run_id=ANY(%s)", (ids,))


def analytical_snapshot(conn):
    tables = conn.execute(
        "SELECT schemaname, tablename FROM pg_tables "
        "WHERE schemaname IN ('dw', 'marts') ORDER BY 1, 2"
    ).fetchall()
    return [
        (
            schema,
            table,
            conn.execute(
                sql.SQL("SELECT * FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchall(),
        )
        for schema, table in tables
    ]


def test_all_files_preserved_and_no_publication(extraction_env, source_files):
    path, rows = source_files
    with connect_database("writer", environ=extraction_env) as conn:
        before = analytical_snapshot(conn)
    run = extract.extract_sources(environ=extraction_env)
    with connect_database("writer", environ=extraction_env) as conn:
        state, phase, pub, manifest, counts = conn.execute(
            "SELECT estado, fase, publication_id, archivos, conteos "
            "FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone()
        assert (state, phase, pub) == ("en_curso", "extraccion_completa", None)
        assert counts == {name: 2 for name in rows} | {"Data_Dictionary.csv": 37}
        for name, (headers, expected) in rows.items():
            table = name.removesuffix(".csv").lower()
            actual = conn.execute(
                sql.SQL(
                    "SELECT numero_registro_origen, {} FROM staging.{} "
                    "WHERE run_id=%s ORDER BY numero_registro_origen"
                ).format(
                    sql.SQL(",").join(map(sql.Identifier, headers)),
                    sql.Identifier(table),
                ),
                (run,),
            ).fetchall()
            assert actual == [(i, *row) for i, row in enumerate(expected, 1)]
        for name, detail in manifest.items():
            assert (
                detail["sha256"]
                == hashlib.sha256((path / name).read_bytes()).hexdigest()
            )
            assert detail["estado"] == "confirmado"
        assert analytical_snapshot(conn) == before
        assert conn.execute(
            "SELECT count(*) FROM ops.incidencias WHERE run_id=%s", (run,)
        ).fetchone() == (0,)


@pytest.mark.parametrize("failure", ["missing", "structure", "modified", "database"])
def test_failure_rolls_back_only_current_file(
    extraction_env, source_files, monkeypatch, failure
):
    path, _ = source_files
    target = path / "Products.csv"
    if failure == "missing":
        target.unlink()
    elif failure == "structure":
        with target.open("a", encoding="utf-8") as handle:
            handle.write("too,few\n")
    elif failure == "database":
        # NUL es texto CSV, pero PostgreSQL TEXT no puede representarlo.
        target.write_bytes(target.read_bytes().replace(b"0001", b"\x00", 1))
    else:
        original = extract.verify_unchanged

        def change(path, raw, fingerprint):
            if path.name == "Products.csv":
                path.write_bytes(path.read_bytes() + b"\n")
            return original(path, raw, fingerprint)

        monkeypatch.setattr(extract, "verify_unchanged", change)
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=extraction_env)
    run = caught.value.run_id
    with connect_database("writer", environ=extraction_env) as conn:
        assert conn.execute(
            "SELECT count(*) FROM staging.customers WHERE run_id=%s", (run,)
        ).fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM staging.products WHERE run_id=%s", (run,)
        ).fetchone() == (0,)
        state, pub, manifest = conn.execute(
            "SELECT estado, publication_id, archivos "
            "FROM ops.ejecuciones WHERE run_id=%s",
            (run,),
        ).fetchone()
        assert (state, pub) == ("fallido", None)
        assert manifest["Customers.csv"]["estado"] == "confirmado"
        assert manifest["Products.csv"]["estado"] == "fallido"
        assert manifest["Sales.csv"]["estado"] == "pendiente"
        file, ordinal, classification = conn.execute(
            "SELECT archivo, numero_registro_origen, clasificacion "
            "FROM ops.incidencias WHERE run_id=%s",
            (run,),
        ).fetchone()
        assert (file, classification) == ("Products.csv", "error_critico")
        if failure == "structure":
            assert ordinal == 3


def test_dictionary_mismatch_blocks_staging(extraction_env, source_files):
    path, _ = source_files
    dictionary = path / "Data_Dictionary.csv"
    dictionary.write_bytes(
        dictionary.read_bytes().replace(b"CustomerKey", b"UnknownKey", 1)
    )
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=extraction_env)
    with connect_database("writer", environ=extraction_env) as conn:
        assert conn.execute(
            "SELECT count(*) FROM staging.customers WHERE run_id=%s",
            (caught.value.run_id,),
        ).fetchone() == (0,)


@pytest.mark.parametrize("fail", [False, True])
def test_run_committed_before_first_read_and_connections_closed(
    extraction_env, source_files, monkeypatch, fail
):
    if fail:
        (source_files[0] / "Products.csv").unlink()
    original_read = extract.read_snapshot
    original_connect = extract.connect_database
    connections = []

    def checked_read(path):
        with connect_database("reader", environ=extraction_env) as conn:
            assert conn.execute(
                "SELECT count(*) FROM ops.ejecuciones "
                "WHERE archivos->'Customers.csv'->>'ruta'=%s",
                (str(path.parent / "Customers.csv"),),
            ).fetchone() == (1,)
        return original_read(path)

    @contextmanager
    def tracked(*args, **kwargs):
        with original_connect(*args, **kwargs) as conn:
            connections.append(conn)
            yield conn

    monkeypatch.setattr(extract, "read_snapshot", checked_read)
    monkeypatch.setattr(extract, "connect_database", tracked)
    if fail:
        with pytest.raises(extract.ExtractionFailed):
            extract.extract_sources(environ=extraction_env)
    else:
        extract.extract_sources(environ=extraction_env)
    assert connections and all(conn.closed for conn in connections)


def test_final_audit_failure_preserves_all_confirmed_files(extraction_env, monkeypatch):
    original = extract.connect_database

    @contextmanager
    def fail_final_update(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            execute = conn.execute

            def maybe_fail(query, *params, **options):
                if "SET fase='extraccion_completa'" in str(query):
                    raise psycopg.OperationalError(
                        "fixture: final metadata unavailable"
                    )
                return execute(query, *params, **options)

            conn.execute = maybe_fail
            yield conn

    monkeypatch.setattr(extract, "connect_database", fail_final_update)
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=extraction_env)
    with connect_database("writer", environ=extraction_env) as conn:
        state, manifest = conn.execute(
            "SELECT estado, archivos FROM ops.ejecuciones WHERE run_id=%s",
            (caught.value.run_id,),
        ).fetchone()
        assert state == "fallido"
        assert all(detail["estado"] == "confirmado" for detail in manifest.values())


def test_uncertain_file_commit_does_not_overwrite_evidence(extraction_env, monkeypatch):
    original = extract.connect_database
    count = 0

    @contextmanager
    def lost_reply(*args, **kwargs):
        nonlocal count
        count += 1
        with original(*args, **kwargs) as conn:
            yield conn
        if count == 4:  # Diccionario y Customers; solo llamadas de extract, no audit.
            raise psycopg.OperationalError("fixture: reply lost after commit")

    monkeypatch.setattr(extract, "connect_database", lost_reply)
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=extraction_env)
    with connect_database("writer", environ=extraction_env) as conn:
        state, phase, manifest, pub = conn.execute(
            "SELECT estado, fase, archivos, publication_id "
            "FROM ops.ejecuciones WHERE run_id=%s",
            (caught.value.run_id,),
        ).fetchone()
        assert (state, phase, pub) == ("en_curso", "commit_no_confirmado", None)
        assert manifest["Customers.csv"]["estado"] == "confirmado"
        assert conn.execute(
            "SELECT count(*) FROM staging.customers WHERE run_id=%s",
            (caught.value.run_id,),
        ).fetchone() == (2,)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "blank_description"])
def test_dictionary_contract_is_complete_and_unique(
    extraction_env, source_files, defect
):
    path, _ = source_files
    target = path / "Data_Dictionary.csv"
    lines = target.read_bytes().splitlines(keepends=True)
    if defect == "missing":
        lines.pop()
    elif defect == "duplicate":
        lines.append(lines[-1])
    else:
        lines[-1] = b"Exchange Rates,Exchange,\r\n"
    target.write_bytes(b"".join(lines))
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=extraction_env)
    with connect_database("writer", environ=extraction_env) as conn:
        detail = conn.execute(
            "SELECT archivos FROM ops.ejecuciones WHERE run_id=%s",
            (caught.value.run_id,),
        ).fetchone()[0]["Data_Dictionary.csv"]
        assert detail["registros_leidos"] == (37 if defect == "duplicate" else 36)
        assert detail["registros"] == 0


def test_start_commit_lost_reply_remains_pending(extraction_env, monkeypatch):
    original = audit.connect_database
    first = True

    @contextmanager
    def lost_start_reply(*args, **kwargs):
        nonlocal first
        with original(*args, **kwargs) as conn:
            yield conn
        if first:
            first = False
            raise psycopg.OperationalError("fixture: start response lost")

    monkeypatch.setattr(audit, "connect_database", lost_start_reply)
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=extraction_env)
    with connect_database("writer", environ=extraction_env) as conn:
        state, phase, manifest = conn.execute(
            "SELECT estado, fase, archivos FROM ops.ejecuciones WHERE run_id=%s",
            (caught.value.run_id,),
        ).fetchone()
        assert (state, phase) == ("en_curso", "commit_no_confirmado")
        assert all(file["estado"] == "pendiente" for file in manifest.values())
