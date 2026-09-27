"""Extracción CSV textual por archivo. No valida la semántica ni escribe en DW."""

import csv
import hashlib
import io
from collections.abc import Iterator, Mapping
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from sales_analytics import audit
from sales_analytics.config import load_data_dir, load_database_url
from sales_analytics.db import connect_database

# Contrato de nombres y orden de columnas, contrastado con Data_Dictionary.csv.
BUSINESS_FIELDS = {
    "Customers": (
        "CustomerKey",
        "Gender",
        "Name",
        "City",
        "State Code",
        "State",
        "Zip Code",
        "Country",
        "Continent",
        "Birthday",
    ),
    "Products": (
        "ProductKey",
        "Product Name",
        "Brand",
        "Color",
        "Unit Cost USD",
        "Unit Price USD",
        "SubcategoryKey",
        "Subcategory",
        "CategoryKey",
        "Category",
    ),
    "Stores": ("StoreKey", "Country", "State", "Square Meters", "Open Date"),
    "Sales": (
        "Order Number",
        "Line Item",
        "Order Date",
        "Delivery Date",
        "CustomerKey",
        "StoreKey",
        "ProductKey",
        "Quantity",
        "Currency Code",
    ),
    "Exchange Rates": ("Date", "Currency", "Exchange"),
}
DICTIONARY = "Data_Dictionary.csv"
SOURCES = {DICTIONARY: (None, ("Table", "Field", "Description"), "utf-8")} | {
    table.replace(" ", "_") + ".csv": (
        table.replace(" ", "_").lower(),
        fields,
        "cp1252" if table == "Customers" else "utf-8",
    )
    for table, fields in BUSINESS_FIELDS.items()
}


class SourceError(ValueError):
    """Defecto estructural localizado sin exponer el contenido de la fila."""

    def __init__(self, message: str, ordinal: int | None = None):
        super().__init__(message)
        self.ordinal = ordinal


class ExtractionFailed(RuntimeError):
    """Error público con identidad y ubicación del diagnóstico persistente."""

    def __init__(self, run_id: UUID, location: str):
        super().__init__(
            f"Extracción bloqueada: run_id={run_id}; diagnóstico: {location}"
        )
        self.run_id = run_id


def iter_records(raw: bytes, headers: tuple[str, ...], encoding: str) -> Iterator:
    try:
        text = raw.decode(encoding, errors="strict")
    except UnicodeError:
        raise SourceError(f"Codificación incompatible con {encoding}.") from None
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    ordinal = None
    try:
        if next(reader, None) != list(headers):
            raise SourceError("Encabezado ausente o diferente del contrato.")
        ordinal = 1
        for row in reader:
            if len(row) != len(headers):
                raise SourceError("Número de campos diferente del encabezado.", ordinal)
            yield ordinal, row
            ordinal += 1
    except csv.Error:
        raise SourceError(
            f"CSV mal formado; línea física final del parser: {reader.line_num}.",
            ordinal,
        ) from None


def _fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_snapshot(path: Path) -> tuple[bytes, tuple]:
    try:
        before = _fingerprint(path)
        raw = path.read_bytes()
        if before != _fingerprint(path):
            raise SourceError("Archivo modificado durante la lectura.")
        return raw, before
    except OSError:
        raise SourceError("No se pudo leer el archivo de origen.") from None


def verify_unchanged(path: Path, raw: bytes, fingerprint: tuple) -> None:
    current, current_fingerprint = read_snapshot(path)
    if (
        current_fingerprint != fingerprint
        or hashlib.sha256(current).digest() != hashlib.sha256(raw).digest()
    ):
        raise SourceError("Archivo modificado durante la extracción.")


def _verify_dictionary(records: Iterator, detail: dict) -> None:
    expected = {
        (table, field) for table, fields in BUSINESS_FIELDS.items() for field in fields
    }
    seen = set()
    for ordinal, (table, field, description) in records:
        pair = (table, field)
        if pair not in expected or pair in seen or not description.strip():
            raise SourceError(
                "Definición ausente, duplicada o ajena al contrato.", ordinal
            )
        seen.add(pair)
        detail["registros_leidos"] = len(seen)
    if seen != expected:
        raise SourceError("El diccionario no cubre todos los campos del contrato.")


def _persist_records(
    conn: psycopg.Connection,
    run_id: UUID,
    table: str,
    headers: tuple,
    records: Iterator,
    detail: dict,
) -> None:
    query = sql.SQL("COPY staging.{} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(",").join(
            map(sql.Identifier, ("run_id", "numero_registro_origen", *headers))
        ),
    )
    with conn.cursor() as cursor, cursor.copy(query) as copy:
        for ordinal, row in records:
            copy.write_row((run_id, ordinal, *row))
            detail["registros_leidos"] = ordinal


def _extract_file(
    run_id: UUID, name: str, detail: dict, environ: Mapping[str, str] | None
) -> None:
    path = Path(detail["ruta"])
    table, headers, encoding = SOURCES[name]
    with connect_database("writer", environ=environ) as conn:
        raw, fingerprint = read_snapshot(path)
        detail["sha256"] = hashlib.sha256(raw).hexdigest()
        detail["bytes"] = len(raw)
        records = iter_records(raw, headers, encoding)
        if table is None:
            _verify_dictionary(records, detail)
        else:
            _persist_records(conn, run_id, table, headers, records, detail)
        verify_unchanged(path, raw, fingerprint)
        detail.update(estado="confirmado", registros=detail["registros_leidos"])
        audit.record_file(conn, run_id, name, detail)
        # Solo un error posterior a este punto puede tener commit incierto.
        detail["commit_enviado"] = True


def extract_sources(
    *,
    environ: Mapping[str, str] | None = None,
    diagnostics_dir: Path = Path(".local/extraction-errors"),
) -> UUID:
    """Retorna el intento extraído; E3 nunca asigna publication_id ni ejecuta E4."""
    data_dir = load_data_dir(environ)
    load_database_url("writer", environ)
    run_id = uuid4()
    manifest = {
        name: {
            "ruta": str(data_dir / name),
            "estado": "pendiente",
            "sha256": None,
            "registros": 0,
            "registros_leidos": 0,
        }
        for name in SOURCES
    }
    name = None
    detail = {}
    closing_commit = False
    try:
        audit.start_run(run_id, manifest, environ)
        for name, detail in manifest.items():
            detail["estado"] = "leyendo"
            with connect_database("writer", environ=environ) as conn:
                audit.record_file(conn, run_id, name, detail)
            _extract_file(run_id, name, detail, environ)
            detail.pop("commit_enviado")
        # Los archivos ya están confirmados: un fallo de cierre no cambia su evidencia.
        name, detail = None, {}
        with connect_database("writer", environ=environ) as conn:
            conn.execute(
                "UPDATE ops.ejecuciones SET fase='extraccion_completa', "
                "ultimo_avance=clock_timestamp() WHERE run_id=%s",
                (run_id,),
            )
            closing_commit = True
        return run_id
    except (SourceError, psycopg.Error, ConnectionError, OSError) as error:
        uncertain = (
            bool(detail.pop("commit_enviado", False))
            or closing_commit
            or isinstance(error, audit.CommitNotConfirmed)
        )
        detail["estado"] = "no_confirmado" if uncertain else "fallido"
        detail["registros"] = None if uncertain else 0
        diagnostic = {
            "regla": "C1" if isinstance(error, SourceError) else "C5",
            "motivo": str(error)
            if isinstance(error, SourceError)
            else (
                f"Fallo de infraestructura ({type(error).__name__}); "
                f"SQLSTATE={getattr(error, 'sqlstate', None)}."
            ),
            "ordinal": getattr(error, "ordinal", None),
            "incierto": uncertain,
        }
        try:
            location = audit.record_failure(
                run_id, name, detail, diagnostic, environ, diagnostics_dir
            )
        except OSError:
            location = "no persistido: PostgreSQL y registro local no disponibles"
        raise ExtractionFailed(run_id, location) from None
