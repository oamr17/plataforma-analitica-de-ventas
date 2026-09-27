"""Identidad del proceso: no usar antigüedad como prueba de interrupción."""

import os
import platform
from datetime import timedelta

from sales_analytics import audit, recovery


def test_current_process_is_alive_and_reused_pid_is_distinguished():
    start = audit.process_started_at()
    assert recovery.process_status(platform.node(), os.getpid(), start) == "activo"
    assert (
        recovery.process_status(
            platform.node(), os.getpid(), start - timedelta(seconds=1)
        )
        == "pid_reutilizado"
    )


def test_unknown_host_does_not_claim_process_death():
    assert (
        recovery.process_status(
            "equipo-inaccesible-E6", os.getpid(), audit.process_started_at()
        )
        == "desconocido"
    )


def test_permission_error_does_not_claim_process_death(monkeypatch):
    def denied(*args):
        raise PermissionError("fixture")

    start = audit.process_started_at()
    monkeypatch.setattr(audit, "process_started_at", denied)
    assert recovery.process_status(platform.node(), os.getpid(), start) == "desconocido"
