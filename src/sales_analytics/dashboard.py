"""Presentación local: navegación y formato sobre un conjunto completo de SQL."""

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import psycopg
import streamlit as st

from sales_analytics.read_dashboard import PAGES, read_dashboard

FILTER_LABELS = {
    "categoria_codigos": "Categoría",
    "product_keys": "Producto",
    "paises_cliente": "País del cliente",
    "store_keys": "Sucursal",
    "paises_sucursal": "País de la sucursal",
    "canales": "Canal",
    "monedas": "Moneda del pedido",
}


def number(value, *, money=False, percent=False) -> str:
    if value is None:
        return "—"
    if money or percent:
        rounded = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        text = f"{rounded:,.2f}"
    else:
        text = f"{value:,}"
    text = text.translate(str.maketrans({",": ".", ".": ","}))
    return ("USD " if money else "") + text + (" %" if percent else "")


def filters_form(bundle: dict) -> None:
    options = bundle["opciones"]
    labels = {
        "product_keys": {r["product_key"]: r["producto"] for r in options["productos"]},
        "categoria_codigos": {
            r["categoria_codigo"]: r["categoria"] for r in options["categorias"]
        },
        "store_keys": {0: "Online"},
    }
    with st.sidebar.form("filtros"):
        st.markdown("### Selección")
        st.date_input(
            "Desde",
            value=bundle["seleccion"]["inicio"],
            key="inicio",
            format="DD/MM/YYYY",
        )
        st.date_input(
            "Hasta", value=bundle["seleccion"]["fin"], key="fin", format="DD/MM/YYYY"
        )
        for key, label in FILTER_LABELS.items():
            # Retener una opción obsoleta permite corregirla explícitamente.
            choices = list(dict.fromkeys(options[key] + st.session_state.get(key, [])))
            mapping = labels.get(key, {})
            st.multiselect(
                label,
                choices,
                key=key,
                placeholder="Seleccionar opciones",
                format_func=lambda v, m=mapping: f"{v} · {m[v]}" if v in m else str(v),
            )
        st.caption("Sin selección = todos. País de la sucursal excluye Online.")
        st.checkbox(
            "Acepto comparar meses históricos con huecos conocidos", key="acepta_huecos"
        )
        st.form_submit_button("Aplicar y actualizar", type="primary", width="stretch")


def metrics(row: dict, fields: list[tuple]) -> None:
    columns = st.columns(len(fields))
    for column, (label, key, kind) in zip(columns, fields, strict=True):
        column.metric(
            label, number(row[key], money=kind == "money", percent=kind == "percent")
        )


def table(rows: list[dict], columns: dict[str, str]) -> None:
    records = []
    formats = {}
    for row in rows:
        record = {}
        for key, label in columns.items():
            value = row.get(key)
            if key.endswith("_usd") or key.endswith("_pct"):
                value = number(
                    value, money=key.endswith("_usd"), percent=key.endswith("_pct")
                )
            elif key == "sin_registros":
                value = "sin registros de ventas" if value else "Con registros"
            elif value is None:
                value = "No aplica"
            elif key == "run_id":
                value = str(value)
            elif isinstance(value, date):
                formats[label] = st.column_config.DateColumn(format="DD/MM/YYYY")
            record[label] = value
        records.append(record)
    st.dataframe(records, column_config=formats, hide_index=True, width="stretch")


def chart(rows: list[dict], x: str, y: str, *, temporal=False) -> None:
    st.vega_lite_chart(
        [{x: str(r[x]), y: float(r[y]) if r[y] is not None else None} for r in rows],
        {
            "height": 280,
            "mark": {"type": "line" if temporal else "bar", "color": "#147d78"},
            "encoding": {
                "x": {
                    "field": x,
                    "type": "temporal" if temporal else "nominal",
                    "title": "Mes" if temporal else "Canal",
                },
                "y": {
                    "field": y,
                    "type": "quantitative",
                    "title": "Ingresos estimados · USD",
                },
                "tooltip": [{"field": x}, {"field": y, "format": ",.2f"}],
            },
        },
        width="stretch",
    )


def executive(bundle: dict) -> None:
    row = bundle["resumen"][0]
    metrics(
        row,
        [
            ("Ingresos estimados", "ingresos_usd", "money"),
            ("Pedidos", "pedidos", "count"),
            ("Unidades registradas", "unidades", "count"),
        ],
    )
    metrics(
        row,
        [
            ("Ticket promedio estimado", "ticket_usd", "money"),
            ("Margen estimado de catálogo", "margen_usd", "money"),
            ("Margen sobre ingresos", "margen_pct", "percent"),
        ],
    )
    st.subheader("Evolución de los ingresos")
    chart(bundle["mes"], "mes", "ingresos_usd", temporal=True)
    st.caption(
        "Estimaciones de catálogo en USD. No representan cobros ni utilidad neta."
    )
    with st.expander("Detalle mensual y crecimiento", expanded=True):
        table(
            bundle["mes"],
            {
                "mes": "Mes",
                "ingresos_usd": "Ingresos estimados",
                "crecimiento_pct": "Crecimiento mensual",
                "pedidos": "Pedidos",
            },
        )
        st.caption(
            "— = no calculable. Meses parciales, anterior sin ingresos o cobertura "
            "no aceptada no muestran MoM estándar."
        )


def products(bundle: dict) -> None:
    st.subheader("Productos por ingresos estimados")
    table(
        bundle["producto"],
        {
            "product_key": "Clave",
            "producto": "Producto",
            "ingresos_usd": "Ingresos estimados",
            "unidades": "Unidades",
            "pedidos": "Pedidos",
        },
    )
    st.subheader("Sucursales y canal")
    chart(bundle["canal"], "clave", "ingresos_usd")
    table(
        bundle["sucursal"],
        {
            "store_key": "Sucursal",
            "canal": "Canal",
            "pais_sucursal": "País de la sucursal",
            "ingresos_usd": "Ingresos estimados",
            "pedidos": "Pedidos",
            "sin_registros": "Estado de la selección",
        },
    )
    st.caption(
        "Sin registros de ventas no significa cerrada o inactiva. Online no tiene "
        "ubicación física. Los pedidos distintos no se suman entre productos."
    )


def clients(bundle: dict) -> None:
    metrics(
        bundle["resumen"][0],
        [
            ("Clientes observados", "clientes", "count"),
            ("Clientes nuevos observados", "nuevos", "count"),
            ("Clientes recurrentes", "recurrentes", "count"),
        ],
    )
    st.info("Nuevos y recurrentes pueden solaparse; no son segmentos excluyentes.")
    st.markdown(
        "**Nuevos observados:** primera compra en todo el histórico dentro del "
        "período, con compra en la selección.\n\n**Recurrentes:** dos o más pedidos "
        "distintos dentro del período y los filtros aplicados."
    )
    table(
        bundle["mes"],
        {
            "mes": "Mes",
            "clientes": "Clientes observados",
            "nuevos": "Nuevos observados",
            "recurrentes": "Recurrentes",
        },
    )
    st.caption(
        "El histórico disponible y sus huecos limitan la interpretación. Los clientes "
        "mensuales no se suman para obtener el total del período."
    )


def quality(bundle: dict) -> None:
    publication = bundle["metadatos"]
    latest = bundle["calidad"]["ultimo_intento"]
    st.subheader("Publicación visible")
    st.markdown(
        f"**Publicación {publication['publication_id']}** · "
        f"Reglas {publication['version_reglas']}"
    )
    st.caption(f"run_id: {publication['run_id']}")
    st.subheader("Último intento de carga")
    if latest:
        st.markdown(f"Estado: **{latest['estado']}** · Fase: **{latest['fase']}**")
        st.caption(
            f"run_id: {latest['run_id']} · "
            f"Publicación: {latest['publication_id'] or 'ninguna'}"
        )
    st.subheader("Incidencias registradas")
    table(
        bundle["calidad"]["totales"],
        {
            "run_id": "Intento",
            "errores": "Errores críticos",
            "rechazos": "Registros rechazados",
            "advertencias": "Advertencias",
        },
    )
    issues = bundle["calidad"]["incidencias"]
    if issues:
        table(
            issues,
            {
                "run_id": "Intento",
                "clasificacion": "Clasificación",
                "regla": "Regla",
                "incidencias": "Incidencias",
            },
        )
    else:
        st.info("Sin incidencias registradas para estos intentos.")
    st.caption(
        "Conteos de incidencias; varias reglas pueden afectar un mismo registro. "
        "No equivalen a registros rechazados distintos."
    )
    st.subheader("Cobertura temporal")
    st.write(
        f"Pedidos observados: {publication['fecha_pedido_min']:%d/%m/%Y} "
        f"a {publication['fecha_pedido_max']:%d/%m/%Y}."
    )
    st.info(
        "Cobertura comercial no verificada. Los huecos no se imputan como ventas; "
        "febrero de 2021 no tiene MoM estándar en el snapshot inicial."
    )


def main() -> None:
    st.set_page_config(page_title="Sales Analytics", page_icon=None, layout="wide")
    st.html("""<style>
        .stMainBlockContainer {max-width: 1260px; padding-top: 2.5rem;}
        h1 {letter-spacing: -.025em;} h2 {margin-top: 1rem;}
        [data-testid="stMetricValue"] {font-size: clamp(1.3rem, 2.3vw, 2.1rem);
                                      font-variant-numeric: tabular-nums;}
        [data-testid="stSidebar"] {background: #f4f7f7;}
        ::selection {background: #bde7df; color: #173b38;}
        </style>""")
    st.sidebar.title("Sales Analytics")
    page = st.sidebar.radio("Análisis", PAGES, label_visibility="collapsed")
    selected = {key: st.session_state.get(key) or None for key in FILTER_LABELS}
    try:
        with st.spinner("Consultando una publicación consistente…"):
            bundle = read_dashboard(
                page,
                st.session_state.get("inicio"),
                st.session_state.get("fin"),
                filters=selected,
                accept_coverage_warnings=st.session_state.get("acepta_huecos", False),
            )
        st.session_state["ultimo_completo"] = bundle
    except (ConnectionError, psycopg.Error, TimeoutError, ValueError) as error:
        logging.getLogger(__name__).warning(
            "Lectura descartada: tipo=%s sqlstate=%s",
            type(error).__name__,
            getattr(error, "sqlstate", None),
        )
        st.error(
            "No se pudo actualizar. Revise la selección, la conexión o sus límites "
            "y vuelva a aplicar los filtros."
        )
        bundle = st.session_state.get("ultimo_completo")
        if bundle is None:
            st.button("Reintentar")
            return
        st.warning(
            "Se conserva el resultado completo anterior, con su página, "
            "publicación y filtros anteriores."
        )
    st.title(bundle["pagina"])
    if bundle["metadatos"] is None:
        st.info("Todavía no hay una publicación analítica confirmada.")
        st.button("Actualizar")
        return
    filters_form(bundle)
    selection = bundle["seleccion"]
    st.caption(
        f"Publicación {bundle['metadatos']['publication_id']} · "
        f"{selection['inicio']:%d/%m/%Y} → {selection['fin']:%d/%m/%Y} · USD"
    )
    st.caption(f"Lectura: {bundle['leido_en']:%d/%m/%Y %H:%M:%S %Z}")
    active = [
        f"{FILTER_LABELS[k]}: {', '.join(map(str, v))}"
        for k, v in selection["filters"].items()
        if v is not None
    ]
    st.caption(
        "Filtros aplicados: "
        + (" · ".join(active) if active else "todos")
        + (
            " · Comparación con huecos aceptada"
            if selection["accept_coverage_warnings"]
            else " · Comparación con huecos no aceptada"
        )
    )
    if bundle["resumen"][0]["pedidos"] == 0:
        st.info(
            "Sin registros para esta selección. Los cocientes sin denominador "
            "se muestran como —."
        )
    {PAGES[0]: executive, PAGES[1]: products, PAGES[2]: clients, PAGES[3]: quality}[
        bundle["pagina"]
    ](bundle)
    st.divider()
    st.caption(
        "Fuente: Global Electronics Retailer · Maven Analytics. Valoración de "
        "catálogo; cobertura comercial no verificada. El ticket bajo filtros "
        "de producto corresponde solo a las líneas seleccionadas."
    )


if __name__ == "__main__":
    main()
