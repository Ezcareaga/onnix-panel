"""Static-asset regression tests for the leads dropdown.

The leads-row status dropdown used to render itself through
`<template x-teleport="body">`. That worked around the parent
`.table-scroll-container`'s `overflow-x-auto`, but the side effect was that
after any HTMX swap (the outerHTML swap of the `<tr>` after POST
/leads/{id}/status, or the innerHTML swap of the whole `<tbody>` fired by
the SSE-driven `refreshTable` in leads.html) Alpine moved the brand-new
template to `<body>` and HTMX never re-scanned `<body>`. The result was a
dropdown whose `hx-post` listeners were live in Alpine but never registered
in HTMX — the second status change of any lead silently did nothing until
a full page reload.

The fix removes `x-teleport` and renders the dropdown inline. The overflow
problem is solved by `position: fixed` on the dropdown, which is
viewport-relative and therefore escapes the table container's overflow
context without needing to move the node out of the DOM. Coordinates are
viewport-relative (`getBoundingClientRect`) — no `window.scrollY` /
`window.scrollX` offsets, which would be wrong for `fixed`.

These tests guard the new structure so a future contributor cannot
reintroduce the bug.
"""
from pathlib import Path

import pytest


LEAD_ROW = Path(__file__).parent.parent / "app/templates/partials/lead_item.html"
APP_JS = Path(__file__).parent.parent / "app/static/js/app.js"
BASE_HTML = Path(__file__).parent.parent / "app/templates/base.html"


@pytest.fixture(scope="module")
def lead_row_html() -> str:
    return LEAD_ROW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base_html() -> str:
    return BASE_HTML.read_text(encoding="utf-8")


class TestLeadRowDropdownInline:
    """The dropdown must render inline (no Alpine teleport)."""

    def test_lead_row_does_not_use_teleport(self, lead_row_html: str):
        # x-teleport moves the dropdown out of the DOM hierarchy that HTMX
        # scans on swap, so the new dropdown's hx-post buttons never get
        # registered. The fix renders the dropdown inline. We assert on the
        # attribute form (`x-teleport=`) so the word may still appear in
        # explanatory comments.
        assert "x-teleport=" not in lead_row_html, (
            "lead_item.html must not use x-teleport — it caused HTMX to lose "
            "track of the dropdown's hx-post buttons after each swap."
        )

    def test_lead_row_dropdown_uses_fixed_positioning(self, lead_row_html: str):
        # The dropdown lives inside a `.table-scroll-container` with
        # `overflow-x-auto`. `position: absolute` would be clipped by that
        # ancestor's overflow. `position: fixed` is viewport-relative and
        # escapes the overflow context.
        assert "position:fixed" in lead_row_html, (
            "Dropdown must use position:fixed so it is not clipped by the "
            "table container's overflow-x-auto. position:absolute will be "
            "clipped because .table-scroll-container creates a positioned "
            "ancestor."
        )
        assert "position:absolute" not in lead_row_html, (
            "Dropdown must not use position:absolute — the table container's "
            "overflow-x-auto would clip it."
        )

    def test_lead_row_toggle_uses_viewport_coords(self, lead_row_html: str):
        # `position: fixed` is viewport-relative. Adding window.scrollY /
        # scrollX (which were correct for position:absolute against <body>)
        # would push the dropdown off-screen.
        assert "window.scrollY" not in lead_row_html, (
            "Dropdown coordinate must not include window.scrollY — "
            "position:fixed is viewport-relative."
        )
        assert "window.scrollX" not in lead_row_html, (
            "Dropdown coordinate must not include window.scrollX — "
            "position:fixed is viewport-relative."
        )

    def test_both_dropdowns_share_one_component(self, lead_row_html: str):
        # Los dos menús (cambiar estado y asignar asesor) tenían el mismo
        # x-data copiado. Cuando el anclaje se rompió, se rompió dos veces.
        # Ahora la lógica vive una sola vez, en el componente de base.html.
        assert lead_row_html.count('x-data="fixedMenu"') == 2, (
            "Los dos dropdowns de la fila deben usar el componente fixedMenu; "
            "si vuelve un x-data inline, la próxima corrección se aplica a uno "
            "solo."
        )
        assert "getBoundingClientRect" not in lead_row_html, (
            "El cálculo de posición pertenece a fixedMenu (base.html), no a la "
            "plantilla de la fila."
        )

    def test_fixed_menu_reposiciona_mientras_esta_abierto(self, base_html: str):
        # position:fixed posiciona contra el viewport: si top/left se calculan
        # una sola vez al abrir, el menú se despega del botón al scrollear.
        # El scroll de .table-scroll-container no burbujea, así que el listener
        # tiene que ir en window con capture=true.
        assert 'addEventListener("scroll", this._reposition, true)' in base_html, (
            "fixedMenu debe recalcular su posición al scrollear, con el "
            "listener en capture — si no, el menú se queda clavado."
        )
        assert 'addEventListener("resize", this._reposition)' in base_html, (
            "fixedMenu debe recalcular su posición al cambiar el tamaño de la "
            "ventana."
        )
        # Voltear hacia arriba cuando abajo no entra: sin esto, un botón cerca
        # del borde inferior abre el menú fuera de la pantalla y no hay forma
        # de llegar a él, porque scrollear tampoco lo mueve.
        assert "roomAbove" in base_html and "roomBelow" in base_html, (
            "fixedMenu debe comparar el espacio arriba y abajo del botón para "
            "voltear el menú."
        )


class TestAppJsCleanupRemoved:
    """The htmx teleport-orphan cleanup is no longer needed — there are no
    teleports left in the panel. Keeping a no-op handler would be dead code
    that future maintainers would have to reason about."""

    def test_appjs_does_not_register_teleport_cleanup(self, app_js: str):
        assert "destroyOrphanTeleports" not in app_js, (
            "destroyOrphanTeleports is obsolete now that the lead_row "
            "dropdown no longer uses x-teleport. Remove the handler — dead "
            "code is a maintenance hazard."
        )
        assert "_x_teleportBack" not in app_js, (
            "_x_teleportBack inspection is obsolete now that no template in "
            "the panel uses x-teleport."
        )
