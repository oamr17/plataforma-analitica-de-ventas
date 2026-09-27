"""Restricciones reales de PostgreSQL; ningún CSV participa en las fixtures."""

from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


def test_only_approved_objects_exist(writer):
    expected = (
        {
            ("staging", n)
            for n in ("customers", "products", "stores", "sales", "exchange_rates")
        }
        | {
            ("dw", n)
            for n in (
                "dim_fecha",
                "dim_cliente",
                "dim_producto",
                "dim_sucursal",
                "fact_ventas",
                "tipos_cambio",
            )
        }
        | {("ops", "ejecuciones"), ("ops", "incidencias")}
    )
    actual = set(
        writer.execute(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname = ANY(%s)",
            (["staging", "dw", "marts", "ops"],),
        ).fetchall()
    )
    assert actual == expected
    assert writer.execute("SELECT to_regnamespace('marts') IS NOT NULL").fetchone()[0]


@pytest.mark.parametrize(
    "table", ["customers", "products", "stores", "sales", "exchange_rates"]
)
def test_staging_preserves_invalid_text_and_duplicate_business_keys(
    writer, run_id, table
):
    columns = writer.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='staging' AND table_name=%s "
        "AND column_name NOT IN ('run_id', 'numero_registro_origen') "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    assert columns and all(kind == "text" for _, kind in columns)
    query = sql.SQL("INSERT INTO staging.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(
            map(
                sql.Identifier,
                ["run_id", "numero_registro_origen"] + [c for c, _ in columns],
            )
        ),
        sql.SQL(", ").join(sql.Placeholder() for _ in range(len(columns) + 2)),
    )
    for ordinal in (1, 2):
        writer.execute(
            query, [run_id, ordinal] + [" NA 00 dato inválido " for _ in columns]
        )
    rows = writer.execute(
        sql.SQL(
            "SELECT {} FROM staging.{} WHERE run_id=%s ORDER BY numero_registro_origen"
        ).format(sql.Identifier(columns[0][0]), sql.Identifier(table)),
        (run_id,),
    ).fetchall()
    assert rows == [(" NA 00 dato inválido ",)] * 2
    with pytest.raises(psycopg.errors.UniqueViolation), writer.transaction():
        writer.execute(query, [run_id, 1] + [""] * len(columns))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), writer.transaction():
        writer.execute(query, [uuid4(), 3] + [""] * len(columns))


def test_multiple_unpublished_runs_and_run_primary_key(writer, run_id):
    ids = [run_id]
    for state in ("en_curso", "fallido", "interrumpido", "sin_cambios"):
        ids.append(uuid4())
        writer.execute(
            "INSERT INTO ops.ejecuciones "
            "(run_id, estado, version_reglas, equipo, pid, inicio_proceso) "
            "VALUES (%s, %s, 'e2', 'test', 1, CURRENT_TIMESTAMP)",
            (ids[-1], state),
        )
    assert (
        writer.execute(
            "SELECT count(*) FROM ops.ejecuciones "
            "WHERE publication_id IS NULL AND run_id=ANY(%s)",
            (ids,),
        ).fetchone()[0]
        == 5
    )
    with pytest.raises(psycopg.errors.UniqueViolation), writer.transaction():
        writer.execute(
            "INSERT INTO ops.ejecuciones SELECT * FROM ops.ejecuciones WHERE run_id=%s",
            (run_id,),
        )


def test_publication_unique_positive_and_tied_to_state(writer, run_id):
    writer.execute(
        "UPDATE ops.ejecuciones SET estado='publicado', publication_id=1 "
        "WHERE run_id=%s",
        (run_id,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation), writer.transaction():
        writer.execute(
            "INSERT INTO ops.ejecuciones (run_id, estado, publication_id, "
            "version_reglas, equipo, pid, inicio_proceso) "
            "VALUES (%s, 'publicado', 1, 'e2', 'test', 1, CURRENT_TIMESTAMP)",
            (uuid4(),),
        )
    for state, publication in (
        ("publicado", None),
        ("publicado", 0),
        ("fallido", 2),
        ("desconocido", None),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), writer.transaction():
            writer.execute(
                "UPDATE ops.ejecuciones SET estado=%s, publication_id=%s "
                "WHERE run_id=%s",
                (state, publication, run_id),
            )


def test_online_nulls_and_values_outside_observed_range(writer, sale):
    assert writer.execute(
        "SELECT store_key, superficie_m2 FROM dw.dim_sucursal"
    ).fetchone() == (0, None)
    assert writer.execute(
        "SELECT fecha_entrega, cantidad, costo_referencia_usd > precio_referencia_usd "
        "FROM dw.fact_ventas"
    ).fetchone() == (None, 11, True)
    writer.execute(
        "UPDATE dw.fact_ventas SET precio_referencia_usd=0, costo_referencia_usd=0"
    )


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        ("UPDATE dw.fact_ventas SET cantidad=0", psycopg.errors.CheckViolation),
        ("UPDATE dw.fact_ventas SET numero_pedido=0", psycopg.errors.CheckViolation),
        ("UPDATE dw.fact_ventas SET linea_pedido=0", psycopg.errors.CheckViolation),
        ("UPDATE dw.fact_ventas SET cantidad=NULL", psycopg.errors.NotNullViolation),
        ("UPDATE dw.fact_ventas SET cliente_id=-1", psycopg.errors.ForeignKeyViolation),
        (
            "UPDATE dw.fact_ventas SET producto_id=-1",
            psycopg.errors.ForeignKeyViolation,
        ),
        (
            "UPDATE dw.fact_ventas SET sucursal_id=-1",
            psycopg.errors.ForeignKeyViolation,
        ),
        (
            "UPDATE dw.fact_ventas SET fecha_pedido='2020-01-02'",
            psycopg.errors.ForeignKeyViolation,
        ),
        (
            "UPDATE dw.fact_ventas SET moneda_pedido='EUR'",
            psycopg.errors.ForeignKeyViolation,
        ),
        (
            "UPDATE dw.fact_ventas SET fecha_entrega='2019-01-01'",
            psycopg.IntegrityError,
        ),
        (
            "UPDATE dw.fact_ventas SET precio_referencia_usd=-1",
            psycopg.errors.CheckViolation,
        ),
        (
            "UPDATE dw.fact_ventas SET costo_referencia_usd='NaN'",
            psycopg.errors.CheckViolation,
        ),
        (
            "INSERT INTO dw.fact_ventas SELECT * FROM dw.fact_ventas",
            psycopg.errors.UniqueViolation,
        ),
        ("UPDATE dw.tipos_cambio SET tasa_original=0", psycopg.errors.CheckViolation),
        ("UPDATE dw.tipos_cambio SET tasa_original=2", psycopg.errors.CheckViolation),
        (
            "INSERT INTO dw.tipos_cambio SELECT * FROM dw.tipos_cambio",
            psycopg.errors.UniqueViolation,
        ),
        (
            "INSERT INTO dw.dim_fecha (fecha) VALUES ('2020-01-01')",
            psycopg.errors.UniqueViolation,
        ),
        (
            "INSERT INTO dw.dim_cliente (customer_key, fecha_nacimiento) "
            "VALUES (1, '1990-01-01')",
            psycopg.errors.UniqueViolation,
        ),
        (
            "INSERT INTO dw.dim_producto "
            "(product_key, precio_catalogo_usd, costo_catalogo_usd) VALUES (1, 1, 1)",
            psycopg.errors.UniqueViolation,
        ),
        (
            "INSERT INTO dw.dim_sucursal (store_key, canal, fecha_apertura) "
            "VALUES (0, 'Online', '2010-01-01')",
            psycopg.errors.UniqueViolation,
        ),
        ("UPDATE dw.dim_sucursal SET superficie_m2=0", psycopg.errors.CheckViolation),
        (
            "UPDATE dw.dim_producto SET precio_catalogo_usd=-1",
            psycopg.errors.CheckViolation,
        ),
    ],
)
def test_dw_constraints(writer, sale, statement, error):
    with pytest.raises(error), writer.transaction():
        writer.execute(statement)


def test_calendar_and_source_codes(writer, dimension_ids):
    assert writer.execute(
        "SELECT anio, mes_numero, trimestre, inicio_mes::text, fin_mes::text "
        "FROM dw.dim_fecha WHERE fecha='2020-01-01'"
    ).fetchone() == (2020, 1, 1, "2020-01-01", "2020-01-31")
    writer.execute(
        "UPDATE dw.dim_cliente SET codigo_estado='NA', codigo_postal='00100'"
    )
    writer.execute(
        "UPDATE dw.dim_producto SET categoria_codigo='01', subcategoria_codigo='0101'"
    )
    assert writer.execute(
        "SELECT codigo_estado, codigo_postal FROM dw.dim_cliente"
    ).fetchone() == ("NA", "00100")
    assert writer.execute(
        "SELECT categoria_codigo, subcategoria_codigo FROM dw.dim_producto"
    ).fetchone() == ("01", "0101")


def test_incidence_requires_run_but_allows_file_level_detail(writer, run_id):
    writer.execute(
        "INSERT INTO ops.incidencias (run_id, regla, clasificacion, motivo) "
        "VALUES (%s, 'C1', 'error_critico', 'fixture')",
        (run_id,),
    )
    assert writer.execute(
        "SELECT numero_registro_origen FROM ops.incidencias WHERE run_id=%s", (run_id,)
    ).fetchone() == (None,)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), writer.transaction():
        writer.execute(
            "INSERT INTO ops.incidencias (run_id, regla, clasificacion, motivo) "
            "VALUES (%s, 'C1', 'error_critico', 'fixture')",
            (uuid4(),),
        )
