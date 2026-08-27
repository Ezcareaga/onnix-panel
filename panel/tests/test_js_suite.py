"""Wrapper pytest para la suite JS (node:test) de tests/js/.

M6.5 T6: la lógica del acumulador de links públicos (link_basket.js) es
JS puro sin DOM; se testea con node --test. Este wrapper la integra a la
suite pytest para que CI/pre-merge la corran siempre.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PANEL_ROOT = Path(__file__).resolve().parent.parent


def test_node_js_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está instalado en este entorno — suite JS no ejecutada")

    # Node 22 trata los args de --test como globs (un directorio pelado no
    # expande) → patrón explícito. Node lo expande solo, sin shell.
    proc = subprocess.run(
        [node, "--test", "tests/js/**/*.test.mjs"],
        cwd=PANEL_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"node --test falló (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
