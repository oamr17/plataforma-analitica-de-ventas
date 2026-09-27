"""Lectura parametrizada: las fórmulas viven exclusivamente en sql/analytics.sql."""

import sysconfig
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def _queries() -> dict[str, str]:
    path = Path(sysconfig.get_path("data")) / "share/sales_analytics/analytics.sql"
    return {
        name.strip(): statement
        for name, statement in (
            section.split("\n", 1)
            for section in path.read_text(encoding="utf-8").split("-- name: ")[1:]
        )
    }


def read_publication(conn: psycopg.Connection) -> dict | None:
    """Primera consulta de datos del ciclo; fija la instantánea del llamador."""
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(_queries()["metadatos"]).fetchone()


def read_options(conn: psycopg.Connection) -> dict:
    """Dominios publicados, independientes de los filtros; sin datos personales."""
    columns = {
        "product_keys": "product_key",
        "categoria_codigos": "categoria_codigo",
        "customer_keys": "customer_key",
        "paises_cliente": "pais_cliente",
        "monedas": "moneda_pedido",
    }
    with conn.cursor(row_factory=dict_row) as cursor:
        expressions = [
            sql.SQL(
                "ARRAY(SELECT DISTINCT {c} FROM base WHERE {c} IS NOT NULL "
                "ORDER BY {c}) AS {key}"
            ).format(c=sql.Identifier(column), key=sql.Identifier(key))
            for key, column in columns.items()
        ]
        result = cursor.execute(
            sql.SQL(
                "WITH base AS MATERIALIZED (SELECT * FROM marts.ventas_base) SELECT {} "
            ).format(sql.SQL(", ").join(expressions))
        ).fetchone()
        stores = cursor.execute(
            "SELECT * FROM marts.sucursales ORDER BY store_key"
        ).fetchall()
        for key, column in (
            ("store_keys", "store_key"),
            ("canales", "canal"),
            ("paises_sucursal", "pais_sucursal"),
        ):
            result[key] = sorted(
                {row[column] for row in stores if row[column] is not None}
            )
        result["productos"] = cursor.execute(
            "SELECT DISTINCT product_key, producto FROM marts.ventas_base "
            "ORDER BY product_key"
        ).fetchall()
        result["categorias"] = cursor.execute(
            "SELECT DISTINCT categoria_codigo, categoria FROM marts.ventas_base "
            "ORDER BY categoria_codigo"
        ).fetchall()
        return result


def read_quality(conn: psycopg.Connection, publication_run) -> dict:
    with conn.cursor(row_factory=dict_row) as cursor:
        latest = cursor.execute(
            "SELECT run_id, estado, fase, publication_id, inicio, fin "
            "FROM ops.ejecuciones ORDER BY inicio DESC, run_id DESC LIMIT 1"
        ).fetchone()
        run_ids = [publication_run, latest["run_id"]] if latest else [publication_run]
        issues = cursor.execute(
            "SELECT run_id, clasificacion, regla, count(*) AS incidencias "
            "FROM ops.incidencias WHERE run_id=ANY(%s::uuid[]) "
            "GROUP BY run_id, clasificacion, regla "
            "ORDER BY run_id, clasificacion, regla",
            (run_ids,),
        ).fetchall()
        totals = cursor.execute(
            "SELECT e.run_id, count(i.incidencia_id) FILTER "
            "(WHERE i.clasificacion='error_critico') AS errores, "
            "count(DISTINCT (i.archivo, i.numero_registro_origen)) FILTER "
            "(WHERE i.clasificacion='rechazo') AS rechazos, "
            "count(i.incidencia_id) FILTER "
            "(WHERE i.clasificacion='advertencia') AS advertencias "
            "FROM ops.ejecuciones e LEFT JOIN ops.incidencias i USING(run_id) "
            "WHERE e.run_id=ANY(%s::uuid[]) GROUP BY e.run_id ORDER BY e.run_id",
            (run_ids,),
        ).fetchall()
        return {"ultimo_intento": latest, "incidencias": issues, "totales": totals}


def read_analytics(
    conn: psycopg.Connection,
    inicio: date,
    fin: date,
    *,
    filters: Mapping[str, list | None] | None = None,
    accept_coverage_warnings: bool = False,
) -> dict:
    """El llamador conserva conexión y transacción; devuelve Decimal sin redondear.

    None omite un filtro; [] selecciona ninguno. Las fechas son inclusivas.
    La consistencia entre metadatos y resultados requiere la instantánea del llamador.
    """
    if type(inicio) is not date or type(fin) is not date or inicio > fin:
        raise ValueError("Se requiere un intervalo inclusivo de fechas válido.")
    names = {
        "product_keys": int,
        "categoria_codigos": str,
        "customer_keys": int,
        "paises_cliente": str,
        "store_keys": int,
        "paises_sucursal": str,
        "canales": str,
        "monedas": str,
    }
    selected = dict(filters or {})
    if selected.keys() - names.keys() or type(accept_coverage_warnings) is not bool:
        raise ValueError("Filtro o aceptación de cobertura desconocidos.")
    for key, value in selected.items():
        if value is not None and (
            not isinstance(value, list) or any(type(v) is not names[key] for v in value)
        ):
            raise ValueError(
                "Cada filtro requiere una lista de valores del tipo previsto."
            )
    params = dict.fromkeys(names)
    params.update(
        selected,
        inicio=inicio,
        fin=fin,
        accept_coverage_warnings=accept_coverage_warnings,
    )
    queries = _queries()
    result = {name: [] for name in ("resumen", "producto", "sucursal", "canal", "mes")}
    with conn.cursor(row_factory=dict_row) as cursor:
        result["metadatos"] = cursor.execute(queries["metadatos"]).fetchone()
        for row in cursor.execute(queries["indicadores"], params).fetchall():
            result[row["grupo"]].append(row)
    return result
