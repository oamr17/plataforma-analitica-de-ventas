"""Interacciones reales de Streamlit con conjuntos completos y deterministas."""

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pytest
from streamlit.testing.v1 import AppTest

from sales_analytics import read_dashboard

APP = Path(__file__).parents[2] / "src/sales_analytics/dashboard.py"


@pytest.fixture
def bundle():
    run = UUID("00000000-0000-0000-0000-000000000001")
    row = dict(
        ingresos_usd=Decimal("500"),
        costos_usd=Decimal("260"),
        margen_usd=Decimal("240"),
        margen_pct=Decimal("48"),
        ticket_usd=Decimal("166.666666666"),
        pedidos=3,
        unidades=7,
        clientes=2,
        nuevos=2,
        recurrentes=1,
    )
    return {
        "leido_en": datetime(2026, 9, 25, tzinfo=UTC),
        "pagina": "Resumen ejecutivo",
        "duracion_segundos": 0.1,
        "metadatos": {
            "publication_id": 1,
            "run_id": run,
            "fecha_pedido_min": date(2020, 1, 1),
            "fecha_pedido_max": date(2021, 2, 20),
            "version_reglas": "fixture",
        },
        "seleccion": {
            "inicio": date(2020, 1, 1),
            "fin": date(2020, 1, 31),
            "filters": {},
            "accept_coverage_warnings": False,
        },
        "opciones": {
            "product_keys": [1],
            "categoria_codigos": ["01"],
            "paises_cliente": ["País"],
            "paises_sucursal": ["País"],
            "store_keys": [0, 1],
            "canales": ["Online", "Físico"],
            "monedas": ["USD"],
            "productos": [{"product_key": 1, "producto": "X"}],
            "categorias": [{"categoria_codigo": "01", "categoria": "Cat"}],
        },
        "resumen": [row],
        "mes": [dict(row, mes=date(2021, 2, 1), crecimiento_pct=None)],
        "producto": [dict(row, product_key=1, producto="X")],
        "sucursal": [
            dict(
                row,
                store_key=0,
                canal="Online",
                pais_sucursal=None,
                sin_registros=False,
            ),
            dict(
                row,
                store_key=1,
                canal="Físico",
                pais_sucursal="País",
                sin_registros=True,
                ingresos_usd=Decimal(0),
            ),
        ],
        "canal": [dict(row, clave="Online")],
        "calidad": {
            "totales": [
                {"run_id": run, "errores": 0, "rechazos": 0, "advertencias": 1}
            ],
            "ultimo_intento": {
                "run_id": UUID(int=2),
                "estado": "fallido",
                "fase": "validacion",
                "publication_id": None,
            },
            "incidencias": [
                {
                    "run_id": run,
                    "clasificacion": "advertencia",
                    "regla": "W5",
                    "incidencias": 1,
                }
            ],
        },
    }


@pytest.fixture
def app(monkeypatch, bundle):
    def read(page, inicio=None, fin=None, **kwargs):
        result = deepcopy(bundle)
        result["pagina"] = page
        return result

    monkeypatch.setattr(read_dashboard, "read_dashboard", read)
    return AppTest.from_file(str(APP)).run(timeout=15)


def test_executive_exact_format(app):
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Ingresos estimados"] == "USD 500,00"
    assert metrics["Ticket promedio estimado"] == "USD 166,67"
    assert metrics["Margen estimado de catálogo"] == "USD 240,00"
    assert "—" in str(app.dataframe[0].value)


def test_chart_contains_plottable_fields(app):
    chart = app.get("vega_lite_chart")[0]
    rows = pa.ipc.open_stream(chart.proto.data.data).read_all().to_pylist()
    assert rows[0]["ingresos_usd"] == 500.0
    assert rows[0]["mes"] == "2021-02-01"


@pytest.mark.parametrize("page", read_dashboard.PAGES)
def test_four_pages(app, page):
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    assert app.title[0].value == page
    if page == "Clientes":
        metrics = {m.label: m.value for m in app.metric}
        assert metrics["Clientes nuevos observados"] == "2"
        assert metrics["Clientes recurrentes"] == "1"
        assert any("solaparse" in v.value for v in app.info)
    if page == "Productos y sucursales":
        assert any("sin registros de ventas" in str(v.value) for v in app.dataframe)
        assert any("Online" in str(v.value) for v in app.dataframe)
    if page == "Calidad y cargas":
        assert any("fallido" in v.value for v in app.markdown)


def test_failed_refresh_keeps_old_labels(app, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("La selección ya no es válida; corrija los filtros.")

    monkeypatch.setattr(read_dashboard, "read_dashboard", fail)
    app.sidebar.radio[0].set_value("Clientes").run()
    assert not app.exception
    assert app.title[0].value == "Resumen ejecutivo"
    assert any("anterior" in v.value for v in app.warning)
    assert any("31/01/2020" in v.value for v in app.caption)


def test_spanish_controls_and_dates_preserve_timestamp(app, bundle):
    assert all(w.proto.placeholder == "Seleccionar opciones" for w in app.multiselect)
    assert all(w.proto.format == "DD/MM/YYYY" for w in app.date_input)
    assert any("25/09/2026 00:00:00 UTC" in v.value for v in app.caption)
    assert app.session_state["ultimo_completo"]["leido_en"] == bundle["leido_en"]
    assert app.dataframe[0].value.iloc[0]["Mes"] == date(2021, 2, 1)
    assert '"format": "DD/MM/YYYY"' in app.dataframe[0].proto.columns


def test_no_publication(monkeypatch):
    monkeypatch.setattr(
        read_dashboard,
        "read_dashboard",
        lambda *a, **k: {
            "pagina": "Resumen ejecutivo",
            "metadatos": None,
        },
    )
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    assert not app.metric
    assert any("publicación" in v.value for v in app.info)


def test_no_sql_cache_or_pipeline_in_ui():
    source = APP.read_text(encoding="utf-8")
    for prohibited in (
        ".execute(",
        "connect_database",
        "cache_data",
        "cache_resource",
        "publish_run",
        "extract_sources",
        "validate_run",
    ):
        assert prohibited not in source
