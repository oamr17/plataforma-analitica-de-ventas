"""Reglas del contrato con registros independientes del snapshot real."""

from copy import deepcopy

import pytest

from sales_analytics.validation import validate_records


def rules(result):
    return {issue["regla"] for issue in result["incidencias"]}


def test_valid_values_and_input_immutability(valid_rows):
    before = deepcopy(valid_rows)
    result = validate_records(valid_rows)
    assert result["apto"] is True
    assert result["rechazados"] == 0
    assert result["aceptados"] == result["entradas"] == 5
    assert "W1" in rules(result)
    assert valid_rows == before


@pytest.mark.parametrize(
    "file,field,value,rule",
    [
        ("Sales.csv", "Order Number", "0", "R1"),
        ("Sales.csv", "Line Item", "1.1", "R1"),
        ("Customers.csv", "CustomerKey", "", "R1"),
        ("Stores.csv", "StoreKey", "-1", "R1"),
        ("Sales.csv", "Quantity", "0", "R3"),
        ("Sales.csv", "Quantity", "-1", "R3"),
        ("Sales.csv", "Quantity", "NA", "R3"),
        ("Sales.csv", "Quantity", "1.1", "R3"),
        ("Products.csv", "Unit Price USD", "$1,23.00", "R4"),
        ("Products.csv", "Unit Cost USD", "-1", "R4"),
        ("Sales.csv", "Order Date", "2/30/2020", "R2"),
        ("Sales.csv", "Delivery Date", "1/1/2020", "R2"),
        ("Customers.csv", "Birthday", "1/3/2020", "R2"),
        ("Stores.csv", "Open Date", "1/3/2020", "R2"),
        ("Sales.csv", "CustomerKey", "999", "R5"),
        ("Sales.csv", "ProductKey", "999", "R5"),
        ("Sales.csv", "StoreKey", "999", "R5"),
        ("Sales.csv", "Currency Code", "", "R6"),
        ("Exchange_Rates.csv", "Exchange", "0", "R6"),
        ("Exchange_Rates.csv", "Exchange", "oops", "R6"),
        ("Stores.csv", "Square Meters", "0", "R7"),
        ("Stores.csv", "Square Meters", "2.5", "R7"),
    ],
)
def test_rejections(valid_rows, file, field, value, rule):
    valid_rows[file][0][field] = value
    result = validate_records(valid_rows)
    assert result["apto"] is False
    assert rule in rules(result)
    assert result["rechazados"] > 0
    assert result["entradas"] == result["aceptados"] + result["rechazados"]


@pytest.mark.parametrize(
    "file",
    ["Customers.csv", "Products.csv", "Stores.csv", "Exchange_Rates.csv", "Sales.csv"],
)
def test_duplicate_keys_are_critical_even_if_identical(valid_rows, file):
    valid_rows[file].append(dict(valid_rows[file][0], numero_registro_origen=2))
    result = validate_records(valid_rows)
    assert {"C2"} <= rules(result)
    assert result["criticos"] > 0 and not result["apto"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("CustomerKey", "2"),
        ("StoreKey", "1"),
        ("Currency Code", "EUR"),
        ("Order Date", "1/3/2020"),
        ("Delivery Date", "1/4/2020"),
    ],
)
def test_order_header_consistency(valid_rows, field, value):
    second = dict(valid_rows["Sales.csv"][0], numero_registro_origen=2)
    second["Line Item"] = "2"
    second[field] = value
    valid_rows["Sales.csv"].append(second)
    assert "C3" in rules(validate_records(valid_rows))


def test_invalid_dimension_impact_is_not_a_second_rejection(valid_rows):
    valid_rows["Products.csv"][0]["Unit Price USD"] = "bad"
    valid_rows["Products.csv"][0]["Unit Cost USD"] = "bad"
    result = validate_records(valid_rows)
    assert result["rechazados"] == 1
    assert result["lineas_afectadas_indirectamente"] == 1
    assert result["entradas"] == 5
    assert result["por_archivo"]["Sales.csv"]["rechazados"] == 0
    assert not result["apto"]


def test_missing_rate_and_usd_contract(valid_rows):
    valid_rows["Exchange_Rates.csv"][0]["Exchange"] = "2"
    assert "C8" in rules(validate_records(valid_rows))
    valid_rows["Exchange_Rates.csv"] = []
    assert "R6" in rules(validate_records(valid_rows))


@pytest.mark.parametrize(
    "field,value",
    [("Category", "Otra"), ("Subcategory", "Otra"), ("CategoryKey", "02")],
)
def test_category_mapping(valid_rows, field, value):
    second = dict(
        valid_rows["Products.csv"][0], numero_registro_origen=2, ProductKey="2"
    )
    second[field] = value
    valid_rows["Products.csv"].append(second)
    assert "C7" in rules(validate_records(valid_rows))


def test_warnings_do_not_reject_valid_outliers(valid_rows):
    customer = valid_rows["Customers.csv"][0]
    customer["Birthday"] = "1/1/2005"
    valid_rows["Products.csv"][0]["Unit Price USD"] = "0"
    valid_rows["Products.csv"][0]["Color"] = ""
    valid_rows["Sales.csv"][0]["Delivery Date"] = ""
    valid_rows["Sales.csv"][0]["Quantity"] = "999"
    second = dict(valid_rows["Sales.csv"][0], numero_registro_origen=2)
    second["Line Item"] = "3"
    valid_rows["Sales.csv"].append(second)
    result = validate_records(valid_rows)
    assert result["apto"] and result["rechazados"] == 0
    assert {"W2", "W3", "W5", "W6", "W7"} <= rules(result)
    assert all(
        "2005" not in str(i) and "Persona sintética" not in str(i)
        for i in result["incidencias"]
    )


def test_unused_catalog_and_repeated_names(valid_rows):
    valid_rows["Customers.csv"].append(
        dict(valid_rows["Customers.csv"][0], numero_registro_origen=2, CustomerKey="2")
    )
    result = validate_records(valid_rows)
    assert "W4" in rules(result) and result["apto"]


def test_duplicate_join_and_count_mismatch(valid_rows):
    valid_rows["Products.csv"].append(
        dict(valid_rows["Products.csv"][0], numero_registro_origen=2)
    )
    assert "C4" in rules(validate_records(valid_rows))


def test_changes_blocked_and_explicit_authorization_warns(valid_rows):
    previous = deepcopy(valid_rows)
    valid_rows["Products.csv"][0]["Unit Price USD"] = "3.00"
    result = validate_records(valid_rows, previous=previous)
    assert "C6" in rules(result) and not result["apto"]
    changes = [
        i["evidencia"]["cambio_id"] for i in result["incidencias"] if i["regla"] == "C6"
    ]
    approved = validate_records(
        valid_rows, previous=previous, authorizations={changes[0]: "aprobacion-fixture"}
    )
    assert approved["apto"] and "W8" in rules(approved)


def test_implicit_deletion_is_blocked(valid_rows):
    previous = deepcopy(valid_rows)
    previous["Customers.csv"].append(
        dict(previous["Customers.csv"][0], numero_registro_origen=2, CustomerKey="2")
    )
    assert "C6" in rules(validate_records(valid_rows, previous=previous))


def test_new_descriptive_value_warns(valid_rows):
    previous = deepcopy(valid_rows)
    valid_rows["Products.csv"].append(
        dict(
            valid_rows["Products.csv"][0],
            numero_registro_origen=2,
            ProductKey="2",
            Color="Novel",
        )
    )
    result = validate_records(valid_rows, previous=previous)
    assert "W7" in rules(result) and result["apto"]


def test_authorization_is_bound_to_exact_change(valid_rows):
    previous = deepcopy(valid_rows)
    valid_rows["Products.csv"][0]["Unit Price USD"] = "3"
    first = validate_records(valid_rows, previous=previous)
    change = next(
        i["evidencia"]["cambio_id"] for i in first["incidencias"] if i["regla"] == "C6"
    )
    valid_rows["Products.csv"][0]["Unit Price USD"] = "4"
    result = validate_records(
        valid_rows, previous=previous, authorizations={change: "fixture"}
    )
    assert not result["apto"] and "C6" in rules(result)


def test_multiple_rejections_count_a_row_once_and_separate_overlap(valid_rows):
    valid_rows["Products.csv"][0]["Unit Price USD"] = "bad"
    valid_rows["Sales.csv"][0]["Quantity"] = "bad"
    valid_rows["Sales.csv"][0]["Order Date"] = "bad"
    result = validate_records(valid_rows)
    assert result["rechazados"] == 2
    assert result["lineas_afectadas_indirectamente"] == 1
    assert result["lineas_afectadas_sin_rechazo_directo"] == 0


def test_invalid_date_does_not_become_empty_delivery_warning(valid_rows):
    valid_rows["Sales.csv"][0]["Delivery Date"] = "bad"
    valid_rows["Stores.csv"][0]["Square Meters"] = "bad"
    result = validate_records(valid_rows)
    assert not {"W1", "W2"} & rules(result)


def test_unknown_currency_needs_contract_review_without_rejecting_row(valid_rows):
    valid_rows["Sales.csv"][0]["Currency Code"] = "NZD"
    valid_rows["Exchange_Rates.csv"][0]["Currency"] = "NZD"
    result = validate_records(valid_rows)
    assert not result["apto"] and result["rechazados"] == 0 and "C8" in rules(result)
    approved = validate_records(valid_rows, reviewed_currencies={"NZD"})
    assert approved["apto"]


def test_new_store_is_not_assumed_physical(valid_rows):
    valid_rows["Stores.csv"][0]["StoreKey"] = "67"
    valid_rows["Sales.csv"][0]["StoreKey"] = "67"
    result = validate_records(valid_rows)
    assert not result["apto"] and result["rechazados"] == 0 and "C8" in rules(result)
    assert "W2" not in rules(result)
    reviewed = validate_records(valid_rows, reviewed_physical_stores={67})
    assert reviewed["apto"] and "W2" in rules(reviewed)


@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_conservation_blocks_lost_lines_units_or_cents(position):
    from sales_analytics.validation import add_issue
    from sales_analytics.validation_checks import check_conservation

    issues = []
    expected = (2, 3, 600, 300)
    actual = list(expected)
    actual[position] -= 1
    check_conservation(
        expected, tuple(actual), lambda *args, **kw: add_issue(issues, *args, **kw)
    )
    assert len(issues) == 1 and issues[0]["regla"] == "C4"


def test_balances_use_exact_cents_without_quantity_limits(valid_rows):
    valid_rows["Sales.csv"][0]["Quantity"] = "2147483647"
    valid_rows["Products.csv"][0]["Unit Price USD"] = "9999999999999999.99"
    valid_rows["Products.csv"][0]["Unit Cost USD"] = "9999999999999999.98"
    second = dict(valid_rows["Sales.csv"][0], numero_registro_origen=2)
    second["Line Item"] = "2"
    valid_rows["Sales.csv"].append(second)
    result = validate_records(valid_rows)
    assert result["apto"] and result["conciliacion_totales"] == "conforme"
