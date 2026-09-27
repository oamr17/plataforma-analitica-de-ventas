"""Selección explícita de credenciales por responsabilidad."""

import pytest

from sales_analytics import config


@pytest.mark.parametrize(
    ("role", "name"),
    [("writer", "SALES_WRITER_DATABASE_URL"), ("reader", "SALES_READER_DATABASE_URL")],
)
def test_database_role_requires_its_own_variable(role, name):
    with pytest.raises(ValueError, match=name):
        config.load_database_url(role, {})


def test_database_roles_select_distinct_credentials():
    values = {
        "SALES_WRITER_DATABASE_URL": "postgresql://writer@localhost/db",
        "SALES_READER_DATABASE_URL": "postgresql://reader@localhost/db",
    }
    assert (
        config.load_database_url("writer", values)
        == values["SALES_WRITER_DATABASE_URL"]
    )
    assert (
        config.load_database_url("reader", values)
        == values["SALES_READER_DATABASE_URL"]
    )


def test_database_configuration_rejects_unknown_role():
    with pytest.raises(ValueError, match="rol"):
        config.load_database_url("admin", {})


def test_database_configuration_hides_invalid_secret():
    with pytest.raises(ValueError, match="SALES_READER_DATABASE_URL") as captured:
        config.load_database_url(
            "reader", {"SALES_READER_DATABASE_URL": "postgresql://u:secret@host:bad/db"}
        )
    assert "secret" not in str(captured.value)


def test_database_configuration_reads_process_environment(monkeypatch):
    monkeypatch.setenv("SALES_READER_DATABASE_URL", "postgresql://reader@localhost/db")
    assert config.load_database_url("reader") == "postgresql://reader@localhost/db"
