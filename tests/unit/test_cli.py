"""El comando manual falla con salida segura antes de conectar sin configuración."""

import json
import subprocess
import sys
from uuid import uuid4

import pytest

from sales_analytics import cli


def test_help_and_missing_configuration_do_not_expose_secrets(monkeypatch):
    monkeypatch.delenv("SALES_WRITER_DATABASE_URL", raising=False)
    result = subprocess.run(
        [sys.executable, "-m", "sales_analytics.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and "publish" in result.stdout
    monkeypatch.setenv("SALES_WRITER_DATABASE_URL", "invalid-secret")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sales_analytics.cli",
            "publish",
            "00000000-0000-0000-0000-000000000001",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "invalid-secret" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_run_identifier_is_rejected_before_connection():
    result = subprocess.run(
        [sys.executable, "-m", "sales_analytics.cli", "publish", "not-a-uuid"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_reconcile_command_reports_pending_without_publication(monkeypatch, capsys):
    run = uuid4()
    monkeypatch.setattr(
        cli,
        "reconcile_run",
        lambda *a, **kw: {
            "run_id": str(run),
            "estado": "en_curso",
            "pendiente": True,
            "publication_id": None,
        },
    )
    assert cli.main(["reconcile", str(run)]) == 1
    assert json.loads(capsys.readouterr().out)["publication_id"] is None


@pytest.mark.parametrize("value", [[], {"bad": "approval"}, {"a" * 64: " "}])
def test_invalid_authorization_file_fails_before_extraction(tmp_path, value, capsys):
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main(["run", "--authorizations", str(path)]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_authorized_run_passes_exact_change_references(tmp_path, monkeypatch, capsys):
    run = uuid4()
    approvals = {"a" * 64: "aprobacion-sintetica-E6"}
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(approvals), encoding="utf-8")
    monkeypatch.setattr(cli, "extract_sources", lambda: run)

    def validate(identifier, *, authorizations):
        assert identifier == run and authorizations == approvals
        return {"apto": True}

    monkeypatch.setattr(cli, "validate_run", validate)
    monkeypatch.setattr(
        cli, "publish_run", lambda r: {"run_id": str(r), "estado": "publicado"}
    )
    assert cli.main(["run", "--authorizations", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == str(run)
