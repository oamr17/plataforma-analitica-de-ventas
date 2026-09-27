"""CSV sintéticos pequeños; nunca se copian datos personales del origen."""

import csv

import pytest


@pytest.fixture
def source_files(tmp_path):
    # Contrato independiente de las constantes de producción.
    fields = {
        "Customers": (
            "CustomerKey|Gender|Name|City|State Code|State|"
            "Zip Code|Country|Continent|Birthday"
        ),
        "Products": (
            "ProductKey|Product Name|Brand|Color|Unit Cost USD|"
            "Unit Price USD|SubcategoryKey|Subcategory|CategoryKey|Category"
        ),
        "Stores": "StoreKey|Country|State|Square Meters|Open Date",
        "Sales": (
            "Order Number|Line Item|Order Date|Delivery Date|CustomerKey|"
            "StoreKey|ProductKey|Quantity|Currency Code"
        ),
        "Exchange Rates": "Date|Currency|Exchange",
    }
    rows = {}
    dictionary = []
    for table, text in fields.items():
        headers = text.split("|")
        name = table.replace(" ", "_") + ".csv"
        encoding = "cp1252" if table == "Customers" else "utf-8"
        row = ["001", " NA ", "", "  ", "inválido"][: len(headers)]
        row += ["0001"] * (len(headers) - len(row))
        if table == "Customers":
            row[2] = 'José €\r\n"Apellido", prueba'
        if table == "Sales":
            row[7] = "no es cantidad"
        if table == "Products":
            row[1] = "東京"
        with (tmp_path / name).open("w", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows([row, row])
        rows[name] = (headers, [row, row])
        dictionary.extend((table, field, "Descripción de prueba") for field in headers)
    with (tmp_path / "Data_Dictionary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["Table", "Field", "Description"])
        writer.writerows(dictionary)
    return tmp_path, rows


@pytest.fixture
def valid_rows():
    return {
        "Customers.csv": [
            dict(
                numero_registro_origen=1,
                CustomerKey="001",
                Gender="Female",
                Name="Persona sintética",
                City="Ciudad",
                **{
                    "State Code": "NA",
                    "State": "Provincia",
                    "Zip Code": "00100",
                    "Country": "País",
                    "Continent": "Continente",
                    "Birthday": "1/1/1990",
                },
            )
        ],
        "Products.csv": [
            dict(
                numero_registro_origen=1,
                ProductKey="01",
                **{
                    "Product Name": "Producto",
                    "Brand": "Marca",
                    "Color": "Red",
                    "Unit Cost USD": " $1.00 ",
                    "Unit Price USD": " $2.00 ",
                    "SubcategoryKey": "0101",
                    "Subcategory": "Sub",
                    "CategoryKey": "01",
                    "Category": "Cat",
                },
            )
        ],
        "Stores.csv": [
            dict(
                numero_registro_origen=1,
                StoreKey="0",
                Country="Online",
                State="Online",
                **{"Square Meters": "", "Open Date": "1/1/2010"},
            )
        ],
        "Exchange_Rates.csv": [
            dict(
                numero_registro_origen=1, Date="1/2/2020", Currency="USD", Exchange="1"
            )
        ],
        "Sales.csv": [
            dict(
                numero_registro_origen=1,
                **{
                    "Order Number": "10",
                    "Line Item": "1",
                    "Order Date": "1/2/2020",
                    "Delivery Date": "1/3/2020",
                    "CustomerKey": "1",
                    "StoreKey": "0",
                    "ProductKey": "1",
                    "Quantity": "11",
                    "Currency Code": "USD",
                },
            )
        ],
    }
