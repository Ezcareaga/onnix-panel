"""Vendor JS self-hosting assertions (M6.5 T4).

Root cause de la galeria rota: browsers que bloquean CDNs externos
(Brave Shields) impiden cargar htmx/idiomorph/sse/alpine desde
unpkg.com / cdn.jsdelivr.net. Estos tests pinnean el contrato:
los 4 vendor scripts se sirven self-hosted desde /static/js/vendor/
y el CSP no incluye CDNs en script-src.
"""
import os
import re

import pytest

VENDOR_DIR = os.path.join(
    os.path.dirname(__file__), "..", "app", "static", "js", "vendor"
)
VENDOR_FILES = [
    "htmx-2.0.4.min.js",
    "idiomorph-ext-0.3.0.min.js",
    "htmx-ext-sse-2.2.4.min.js",
    "alpinejs-3.15.9.min.js",
]


@pytest.mark.parametrize("fname", VENDOR_FILES)
def test_vendor_file_exists_and_nonempty(fname):
    path = os.path.join(VENDOR_DIR, fname)
    assert os.path.isfile(path), f"missing vendor file: {path}"
    # htmx ~50KB, alpine ~46KB, sse ~8.9KB, idiomorph-ext ~8.4KB (tamaños reales upstream)
    assert os.path.getsize(path) > 5_000, f"vendor file suspiciously small: {path}"


async def test_no_cdn_scripts_in_base(admin_client):
    resp = await admin_client.get("/properties")
    assert resp.status_code == 200
    html = resp.text
    assert "unpkg.com" not in html
    assert "cdn.jsdelivr.net" not in html
    for f in VENDOR_FILES:
        assert f"/static/js/vendor/{f}" in html, f"vendor script not referenced: {f}"


async def test_csp_has_no_cdn_script_src(admin_client):
    resp = await admin_client.get("/properties")
    csp = resp.headers["content-security-policy"]
    script_src = re.search(r"script-src ([^;]+)", csp).group(1)
    assert "unpkg.com" not in script_src
    assert "jsdelivr" not in script_src
