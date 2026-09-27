"""Candidato en memoria; sin escrituras ni conversión cambiaria."""

from datetime import timedelta

from sales_analytics.validation import convert_records

TRANSFORM_VERSION = "carga-v1"

# Orden de columnas del candidato; las claves de hechos aún son naturales.
COLUMNS = {
    "dim_cliente": (
        "customer_key",
        "genero",
        "nombre",
        "ciudad",
        "codigo_estado",
        "estado",
        "codigo_postal",
        "pais",
        "continente",
        "fecha_nacimiento",
    ),
    "dim_producto": (
        "product_key",
        "nombre",
        "marca",
        "color",
        "subcategoria_codigo",
        "subcategoria",
        "categoria_codigo",
        "categoria",
        "precio_catalogo_usd",
        "costo_catalogo_usd",
    ),
    "dim_sucursal": (
        "store_key",
        "pais_origen",
        "estado_origen",
        "superficie_m2",
        "fecha_apertura",
        "canal",
    ),
    "dim_fecha": ("fecha",),
    "tipos_cambio": ("fecha", "moneda", "tasa_original"),
    "fact_ventas": (
        "numero_pedido",
        "linea_pedido",
        "fecha_pedido",
        "fecha_entrega",
        "cliente_id",
        "producto_id",
        "sucursal_id",
        "cantidad",
        "moneda_pedido",
        "precio_referencia_usd",
        "costo_referencia_usd",
    ),
}


def _optional(value: str) -> str | None:
    return value if value.strip() else None


def transform_records(records: dict) -> dict[str, list[tuple]]:
    """Recibe un lote validado; reutiliza su interpretación sin cambiar el texto."""
    issues = []
    typed = convert_records(records, issues)
    if issues:
        raise ValueError(
            "El lote contiene valores no interpretables; requiere validar."
        )
    products = {r["ProductKey"]: r for r in typed["Products.csv"]}
    result = {table: [] for table in COLUMNS}
    for row in typed["Customers.csv"]:
        result["dim_cliente"].append(
            (
                row["CustomerKey"],
                *(
                    _optional(row[f])
                    for f in (
                        "Gender",
                        "Name",
                        "City",
                        "State Code",
                        "State",
                        "Zip Code",
                        "Country",
                        "Continent",
                    )
                ),
                row["Birthday"],
            )
        )
    for row in typed["Products.csv"]:
        result["dim_producto"].append(
            (
                row["ProductKey"],
                *(
                    _optional(row[f])
                    for f in (
                        "Product Name",
                        "Brand",
                        "Color",
                        "SubcategoryKey",
                        "Subcategory",
                        "CategoryKey",
                        "Category",
                    )
                ),
                row["Unit Price USD"],
                row["Unit Cost USD"],
            )
        )
    for row in typed["Stores.csv"]:
        result["dim_sucursal"].append(
            (
                row["StoreKey"],
                _optional(row["Country"]),
                _optional(row["State"]),
                row["Square Meters"],
                row["Open Date"],
                "Online" if row["StoreKey"] == 0 else "Físico",
            )
        )
    dates = set()
    for row in typed["Exchange_Rates.csv"]:
        dates.add(row["Date"])
        result["tipos_cambio"].append((row["Date"], row["Currency"], row["Exchange"]))
    for row in typed["Sales.csv"]:
        product = products[row["ProductKey"]]
        dates.add(row["Order Date"])
        if row["Delivery Date"] is not None:
            dates.add(row["Delivery Date"])
        result["fact_ventas"].append(
            (
                row["Order Number"],
                row["Line Item"],
                row["Order Date"],
                row["Delivery Date"],
                row["CustomerKey"],
                row["ProductKey"],
                row["StoreKey"],
                row["Quantity"],
                row["Currency Code"],
                product["Unit Price USD"],
                product["Unit Cost USD"],
            )
        )
    if dates:
        first, last = min(dates), max(dates)
        result["dim_fecha"] = [
            (first + timedelta(days=i),) for i in range((last - first).days + 1)
        ]
    return result
