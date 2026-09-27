"""Validación local de configuración, sin acceso a PostgreSQL."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    """Valores validados; la representación evita mostrar credenciales de la URI."""

    data_dir: Path
    database_url: str = field(repr=False)


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value.strip():
        raise ValueError(f"{name}: variable obligatoria ausente o vacía.")
    return value


def _validate_database_url(value: str, name: str = "SALES_DATABASE_URL") -> None:
    message = f"{name}: URI no válida o fuera del formato admitido."
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError(message)
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError(message)
    try:
        uri = urlsplit(value)
        host = uri.hostname
        port = uri.port
    except ValueError:
        # El parser puede incluir el valor recibido en su excepción original.
        raise ValueError(message) from None
    authority = uri.netloc.rsplit("@", 1)[-1]
    if (
        uri.scheme not in {"postgres", "postgresql"}
        or not host
        or any(char in authority for char in ",\\")
        or uri.netloc.count("@") > 1
        or authority.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
        or not uri.path.startswith("/")
        or len(uri.path) == 1
        or uri.path.count("/") != 1
        or "?" in value
        or "#" in value
    ):
        raise ValueError(message)


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    """Lee variables del proceso o un mapa explícito; no lee .env ni usa la red."""
    source = os.environ if environ is None else environ
    data_dir = load_data_dir(source)
    database_url = _required(source, "SALES_DATABASE_URL")
    _validate_database_url(database_url)
    return Config(data_dir=data_dir, database_url=database_url)


def load_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Comparte la validación de la ruta entre E1 y la extracción con rol escritor."""
    source = os.environ if environ is None else environ
    raw_path = _required(source, "SALES_DATA_DIR")
    try:
        data_dir = Path(raw_path).expanduser().resolve(strict=True)
        if not data_dir.is_dir():
            raise ValueError("No es un directorio.")
    except (OSError, ValueError, RuntimeError):
        raise ValueError(
            "SALES_DATA_DIR: debe indicar un directorio existente."
        ) from None
    return data_dir


def load_database_url(role: str, environ: Mapping[str, str] | None = None) -> str:
    """Selecciona una credencial explícita; nunca recurre al usuario administrador."""
    names = {
        "writer": "SALES_WRITER_DATABASE_URL",
        "reader": "SALES_READER_DATABASE_URL",
    }
    if role not in names:
        raise ValueError("rol de conexión desconocido; utilizar writer o reader.")
    source = os.environ if environ is None else environ
    name = names[role]
    value = _required(source, name)
    _validate_database_url(value, name)
    return value
