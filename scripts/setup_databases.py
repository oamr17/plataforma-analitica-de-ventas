"""Preparación administrativa inicial, sin cargar datos ni migrar esquemas."""

import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]

DATABASES = {
    "sales_analytics": ("sales_etl", "sales_reader"),
    "sales_analytics_test": ("sales_test_etl", "sales_test_reader"),
}

SECRET_FILE = ROOT / ".local" / "connections.json"


def prepare(admin: psycopg.Connection, databases: dict, secret_file: Path) -> None:
    """Crea solamente nombres nuevos; conserva credenciales si hay un fallo parcial."""

    if secret_file.exists():
        raise ValueError("Ya existe el archivo privado; no se reemplazará.")

    roles = [role for pair in databases.values() for role in pair]

    if (
        admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = ANY(%s)", (roles,)
        ).fetchone()
        or admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = ANY(%s)", (list(databases),)
        ).fetchone()
    ):
        raise ValueError("Existen nombres reservados por E2; revisar sin borrar datos.")

    passwords = {role: secrets.token_urlsafe(32) for role in roles}

    info = admin.info

    urls = {
        db: {
            kind: f"postgresql://{role}:{quote(passwords[role], safe='')}"
            f"@{info.host}:{info.port}/{db}"
            for kind, role in zip(("writer", "reader"), pair, strict=True)
        }
        for db, pair in databases.items()
    }

    # .local debe tener ACL privada antes de ejecutar este programa.

    with secret_file.open("x", encoding="utf-8") as target:
        json.dump(urls, target, indent=2)

    for role, password in passwords.items():
        verifier = admin.pgconn.encrypt_password(
            password.encode(), role.encode(), b"scram-sha-256"
        ).decode()

        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(verifier))
        )

    for db, pair in databases.items():
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(db)
            )
        )

        admin.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db))
        )

        for role in pair:
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(db), sql.Identifier(role)
                )
            )


def apply_schema(admin_url: str, databases: dict) -> None:
    """Aplica DDL inicial y privilegios; rechaza bases que ya contienen el modelo."""

    ddl = (ROOT / "sql" / "001_schema.sql").read_text(encoding="utf-8")

    for db in databases:
        with psycopg.connect(admin_url, dbname=db) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = ANY(%s)",
                (["staging", "dw", "marts", "ops"],),
            ).fetchone()

            if exists:
                raise ValueError("El esquema ya existe; no se reemplazará.")

    for db, (writer, reader) in databases.items():
        with psycopg.connect(admin_url, dbname=db) as conn:
            conn.execute(ddl)

            conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")

            for schema in ("staging", "dw", "ops"):
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(schema), sql.Identifier(writer)
                    )
                )

                conn.execute(
                    sql.SQL(
                        "GRANT SELECT, INSERT, UPDATE, DELETE "
                        "ON ALL TABLES IN SCHEMA {} TO {}"
                    ).format(sql.Identifier(schema), sql.Identifier(writer))
                )

                conn.execute(
                    sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                        sql.Identifier(schema), sql.Identifier(writer)
                    )
                )

            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA marts, ops TO {}").format(
                    sql.Identifier(reader)
                )
            )

            conn.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA ops TO {}").format(
                    sql.Identifier(reader)
                )
            )

            conn.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA marts "
                    "GRANT SELECT ON TABLES TO {}"
                ).format(sql.Identifier(reader))
            )


def apply_marts(admin_url: str, databases: dict) -> None:
    """Despliega solo vistas y permisos de lectura, sin modificar publicaciones."""
    ddl = (ROOT / "sql" / "002_marts.sql").read_text(encoding="utf-8")
    for database, (writer, reader) in databases.items():
        with psycopg.connect(admin_url, dbname=database) as conn:
            conn.execute("SET LOCAL lock_timeout='5s'")
            if not conn.execute(
                "SELECT pg_try_advisory_xact_lock(53415001)"
            ).fetchone()[0]:
                raise ValueError(
                    "Existe un publicador; desplegar vistas fuera de la carga."
                )
            conn.execute(ddl)
            for role in (writer, reader):
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA marts TO {}").format(
                        sql.Identifier(role)
                    )
                )
                conn.execute(
                    sql.SQL(
                        "GRANT SELECT ON marts.ventas_base,marts.pedidos,"
                        "marts.clientes_primera_compra,marts.sucursales TO {}"
                    ).format(sql.Identifier(role))
                )


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("action", choices=("prepare", "schema", "marts"))

    parser.add_argument(
        "--isolated-tests",
        action="store_true",
        help="Crear solo sales_analytics_e6_test, sin tocar aceptación.",
    )

    args = parser.parse_args()

    databases = (
        {"sales_analytics_e6_test": ("sales_e6_etl", "sales_e6_reader")}
        if args.isolated_tests
        else DATABASES
    )

    secret_file = (
        ROOT / ".local" / "e6-connections.json" if args.isolated_tests else SECRET_FILE
    )

    admin_url = os.environ.get("SALES_ADMIN_DATABASE_URL", "")

    params = conninfo_to_dict(admin_url)

    if params.get("host") != "127.0.0.1" or params.get("dbname") != "postgres":
        raise ValueError("Se requiere el administrador del clúster local de E2.")

    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
        directory = Path(admin.execute("SHOW data_directory").fetchone()[0]).resolve()

        if directory != (ROOT / ".local" / "pgdata").resolve():
            raise ValueError(
                "El clúster no pertenece al directorio local del proyecto."
            )

        admin.execute("SET log_statement = 'none'")

        admin.execute("SET log_min_error_statement = 'panic'")

        if args.action == "prepare":
            prepare(admin, databases, secret_file)

        elif args.action == "schema":
            apply_schema(admin_url, databases)
        else:
            apply_marts(admin_url, databases)

    print("Preparación administrativa completada; no se han cargado datos.")


if __name__ == "__main__":
    try:
        main()

    except (psycopg.Error, ValueError, OSError):
        raise SystemExit(
            "Preparación no completada. Revisar acceso, clúster y objetos existentes; "
            "no se imprimen credenciales ni se reintenta automáticamente."
        ) from None
