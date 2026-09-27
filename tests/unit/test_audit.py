"""Identidad real del proceso y diagnóstico local sin credenciales."""

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sales_analytics import audit, extract


def test_process_identity_is_stable_and_not_call_time():
    first = audit.process_started_at()
    second = audit.process_started_at()
    assert first == second < datetime.now(UTC)
    assert os.getpid() > 0


def test_repeated_local_diagnostics_preserve_previous_evidence(tmp_path):
    run = uuid4()
    first = audit.write_local_diagnostic(run, {"motivo": "primero"}, tmp_path)
    second = audit.write_local_diagnostic(run, {"motivo": "segundo"}, tmp_path)
    assert first != second
    assert json.loads((tmp_path / f"{run}.json").read_text())["motivo"] == "primero"


def test_failed_start_writes_local_diagnostic_before_reading(tmp_path, monkeypatch):
    @contextmanager
    def unavailable(*args, **kwargs):
        raise ConnectionError("sensitive-fixture-password must not be logged")
        yield  # pragma: no cover

    def forbidden(*args):
        pytest.fail("No debe leer sin inicio confirmado")

    monkeypatch.setattr(audit, "connect_database", unavailable)
    monkeypatch.setattr(extract, "read_snapshot", forbidden)
    env = {
        "SALES_DATA_DIR": str(tmp_path),
        "SALES_WRITER_DATABASE_URL": "postgresql://host.invalid/sales_analytics_test",
    }
    directory = tmp_path / "diagnostics"
    with pytest.raises(extract.ExtractionFailed) as caught:
        extract.extract_sources(environ=env, diagnostics_dir=directory)
    saved = (directory / f"{caught.value.run_id}.json").read_text(encoding="utf-8")
    evidence = json.loads(saved)
    assert evidence["run_id"] == str(caught.value.run_id)
    assert evidence["regla"] == "C5"
    assert evidence["incierto"] is False
    assert "sensitive-fixture-password" not in saved + str(caught.value)


def test_diagnostic_disk_error_is_not_reported_as_saved(tmp_path, monkeypatch):
    @contextmanager
    def unavailable(*args, **kwargs):
        raise ConnectionError("fixture")
        yield  # pragma: no cover

    monkeypatch.setattr(audit, "connect_database", unavailable)
    target = tmp_path / "not_a_directory"
    target.write_text("fixture", encoding="utf-8")
    env = {
        "SALES_DATA_DIR": str(tmp_path),
        "SALES_WRITER_DATABASE_URL": "postgresql://host.invalid/sales_analytics_test",
    }
    with pytest.raises(extract.ExtractionFailed, match="no persistido"):
        extract.extract_sources(environ=env, diagnostics_dir=target)


def test_diagnostic_without_database_preserves_file_evidence(tmp_path, monkeypatch):
    @contextmanager
    def unavailable(*args, **kwargs):
        raise ConnectionError("fixture")
        yield  # pragma: no cover

    monkeypatch.setattr(audit, "connect_database", unavailable)
    run = uuid4()
    location = audit.record_failure(
        run,
        "Sales.csv",
        {"sha256": "abc", "registros": 0},
        {"incierto": False, "ordinal": 3, "regla": "C1", "motivo": "CSV mal formado"},
        {},
        tmp_path,
    )
    assert json.loads((tmp_path / f"{run}.json").read_text()) == {
        "incierto": False,
        "ordinal": 3,
        "regla": "C1",
        "motivo": "CSV mal formado",
        "run_id": str(run),
        "archivo": "Sales.csv",
        "origen": {"sha256": "abc", "registros": 0},
    }
    assert location == str(tmp_path / f"{run}.json")
