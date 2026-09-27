"""El modo de aislamiento se selecciona explícitamente, nunca por defecto."""

import subprocess
import sys
from pathlib import Path


def test_setup_exposes_explicit_isolated_test_mode():
    script = Path(__file__).resolve().parents[2] / "scripts/setup_databases.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--isolated-tests" in result.stdout
