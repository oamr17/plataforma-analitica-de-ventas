"""Ejecución manual; credenciales exclusivamente desde el entorno."""

import argparse
import json
import re
import sys
from pathlib import Path
from uuid import UUID

import psycopg

from sales_analytics.extract import ExtractionFailed, extract_sources
from sales_analytics.load import PublicationFailed, publish_run
from sales_analytics.recovery import reconcile_run
from sales_analytics.validation_run import ValidationFailed, validate_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga manual de Sales Analytics.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Extraer, validar y publicar un intento nuevo."
    )
    run.add_argument(
        "--authorizations",
        type=Path,
        help="JSON cambio SHA-256 → referencia de aprobación.",
    )
    publish = commands.add_parser(
        "publish", help="Continuar un intento validado local."
    )
    publish.add_argument("run_id", type=UUID)
    reconcile = commands.add_parser(
        "reconcile", help="Reconciliar manualmente un intento."
    )
    reconcile.add_argument("run_id", type=UUID)
    reconcile.add_argument("--diagnostic", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "reconcile":
            result = reconcile_run(args.run_id, diagnostic_path=args.diagnostic)
            print(json.dumps(result, ensure_ascii=True))
            return int(result.get("pendiente", False))
        if args.command == "run":
            authorizations = {}
            if args.authorizations:
                authorizations = json.loads(
                    args.authorizations.read_text(encoding="utf-8")
                )
                if not isinstance(authorizations, dict) or any(
                    not re.fullmatch("[a-f0-9]{64}", key)
                    or not isinstance(value, str)
                    or not value.strip()
                    for key, value in authorizations.items()
                ):
                    raise ValueError("Autorizaciones sin identidad o referencia.")
            run_id = extract_sources()
            validation = validate_run(run_id, authorizations=authorizations)
            if not validation["apto"]:
                print(
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "estado": "fallido",
                            "fase": "validacion_bloqueada",
                        }
                    )
                )
                return 1
        else:
            run_id = args.run_id
        result = publish_run(run_id)
    except (ExtractionFailed, ValidationFailed, PublicationFailed) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError, psycopg.Error):
        print(
            "Configuración o intento no válido; revisar entorno y auditoría local.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
