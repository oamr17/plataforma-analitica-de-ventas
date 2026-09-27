"""Contrato local: entradas válidas, errores diagnosticables y secretos ocultos."""

import socket
import traceback

import pytest

from sales_analytics.config import load_config


@pytest.fixture
def values(tmp_path):
    return {
        "SALES_DATA_DIR": str(tmp_path),
        "SALES_DATABASE_URL": "postgresql://host.invalid:5432/ventas",
    }


def test_valid_configuration_does_not_use_network(values, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("E1 intentó usar la red")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = load_config(values)
    assert result.data_dir.is_dir()
    assert result.database_url == "postgresql://host.invalid:5432/ventas"


def test_reads_environment_and_resolves_relative_path(tmp_path, monkeypatch):
    (tmp_path / "data local").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SALES_DATA_DIR", "data local")
    monkeypatch.setenv("SALES_DATABASE_URL", "postgres://localhost/ventas")
    result = load_config()
    assert result.data_dir == tmp_path / "data local"
    assert result.database_url == "postgres://localhost/ventas"


@pytest.mark.parametrize("name", ["SALES_DATA_DIR", "SALES_DATABASE_URL"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_required_value(values, name, value):
    if value is None:
        values.pop(name)
    else:
        values[name] = value
    with pytest.raises(ValueError, match=name):
        load_config(values)


@pytest.mark.parametrize("kind", ["absent", "file"])
def test_data_path_must_be_existing_directory(values, tmp_path, kind):
    target = tmp_path / "input"
    if kind == "file":
        target.write_text("fixture", encoding="utf-8")
    values["SALES_DATA_DIR"] = str(target)
    with pytest.raises(ValueError, match="SALES_DATA_DIR"):
        load_config(values)


@pytest.mark.parametrize(
    "uri",
    [
        "https://localhost/ventas",
        "host=localhost dbname=ventas",
        "postgresql:///ventas",
        "postgresql://localhost",
        "postgresql://localhost/",
        "postgresql://localhost:abc/ventas",
        "postgresql://localhost:0/ventas",
        "postgresql://localhost:65536/ventas",
        "postgresql://localhost:/ventas",
        "postgresql://[broken/ventas",
        "postgresql://local host/ventas",
        "postgresql://localhost/ventas\n",
        "postgresql://localhost/ventas/otra",
        "postgresql://localhost/ventas?sslmode=require",
        "postgresql://localhost/ventas#fragment",
        "postgresql://user:bad%ZZ@localhost/ventas",
        "postgresql://host1,host2/ventas",
    ],
)
def test_rejects_malformed_or_unsupported_uri(values, uri):
    values["SALES_DATABASE_URL"] = uri
    with pytest.raises(ValueError, match="SALES_DATABASE_URL"):
        load_config(values)


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://localhost:1/ventas",
        "postgresql://localhost:65535/ventas",
        "postgres://[::1]:5432/ventas",
        "postgresql://usuario:p%40ss@host.invalid/base%20ventas",
    ],
)
def test_accepts_supported_uri_without_rewriting(values, uri):
    values["SALES_DATABASE_URL"] = uri
    assert load_config(values).database_url == uri


def test_secrets_are_hidden_in_normal_representation(values, capsys):
    values["SALES_DATABASE_URL"] = "postgresql://user:sentinel-secret@localhost/ventas"
    result = load_config(values)
    assert result.database_url == values["SALES_DATABASE_URL"]
    assert "sentinel-secret" not in repr(result)
    assert "sentinel-secret" not in str(result)
    assert capsys.readouterr() == ("", "")


def test_parser_errors_do_not_expose_secrets(values):
    values["SALES_DATABASE_URL"] = "postgresql://user:password@localhost:secret-port/db"
    with pytest.raises(ValueError, match="SALES_DATABASE_URL") as captured:
        load_config(values)
    diagnostic = "".join(traceback.format_exception(captured.value))
    assert "secret-port" not in diagnostic
    assert values["SALES_DATABASE_URL"] not in diagnostic


def test_explicit_empty_mapping_does_not_fall_back_to_environment(values, monkeypatch):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="SALES_DATA_DIR"):
        load_config({})
