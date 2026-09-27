"""Comparación de snapshots y autorizaciones específicas; nunca aplica cambios."""

import hashlib
import json


def check_changes(
    current: dict,
    previous: dict | None,
    keys: dict,
    descriptive: dict,
    authorizations: dict,
    add,
) -> list:
    if previous is None:
        return []
    applied = []
    for file, rows in current.items():
        prior = {tuple(r[f] for f in keys[file]): r for r in previous[file]}
        candidate = {tuple(r[f] for f in keys[file]): r for r in rows}
        for key, old in prior.items():
            new = candidate.get(key)
            before = {f: v for f, v in old.items() if f != "numero_registro_origen"}
            after = (
                {f: v for f, v in new.items() if f != "numero_registro_origen"}
                if new
                else None
            )
            if before == after:
                continue
            change_id = hashlib.sha256(
                json.dumps(
                    [file, before, after],
                    sort_keys=True,
                    ensure_ascii=True,
                    default=str,
                ).encode()
            ).hexdigest()
            ordinal = new["numero_registro_origen"] if new else None
            if not authorizations.get(change_id):
                add(
                    file,
                    ordinal,
                    "C6",
                    "Cambio o ausencia de clave previa sin autorización.",
                    cambio_id=change_id,
                    eliminacion=new is None,
                    registro_previo=old["numero_registro_origen"],
                )
            else:
                approval = {
                    "cambio_id": change_id,
                    "autorizacion": authorizations[change_id],
                }
                applied.append(approval)
                changed = {
                    f for f in before if after is not None and before[f] != after[f]
                }
                if changed & (
                    set(descriptive.get(file, ())) | {"Unit Price USD", "Unit Cost USD"}
                ):
                    add(
                        file,
                        ordinal,
                        "W8",
                        "Cambio autorizado con efecto sobre el histórico.",
                        **approval,
                    )
        for field in descriptive.get(file, ()):
            known = {r[field] for r in previous[file] if r[field].strip()}
            for row in rows:
                if row[field].strip() and row[field] not in known:
                    add(
                        file,
                        row["numero_registro_origen"],
                        "W7",
                        "Valor descriptivo nuevo respecto a la referencia.",
                        campo=field,
                    )
    return applied
