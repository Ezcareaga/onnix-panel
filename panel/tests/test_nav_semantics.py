"""El menu lateral: estado actual anunciado, y cerrado no significa navegable.

Dos hallazgos del audit sobre la misma pieza:

1. **Un solo `aria-current` en los 61 templates.** El item activo del menu se
   distinguia solo por color de fondo, asi que para un lector de pantalla los
   ocho items eran indistinguibles: no habia forma de saber en que pantalla
   estabas.
2. **El menu cerrado seguia en el orden de tabulacion.** En movil solo se corre
   con `translateX(-100%)`, que lo saca de la vista pero no del foco: con Tab
   se recorrian los ocho items de un menu que no esta en pantalla, a ciegas.
"""
from __future__ import annotations

import re
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
_SIDEBAR = (_TEMPLATES / "partials" / "sidebar.html").read_text(encoding="utf-8")
_BASE = (_TEMPLATES / "base.html").read_text(encoding="utf-8")

# Los <a> del menu son los que viven adentro del <nav>, no los que tienen cierto
# padding: identificarlos por `px-4 py-2.5` ataba este test a un valor de
# espaciado, y cuando los items subieron a 44px de area tactil el regex dejo de
# encontrar ocho items y el test fallo por algo que no mide.
_NAV = re.compile(r'<nav[^>]*aria-label="Menu principal".*?</nav>', re.DOTALL)
_ITEMS = re.compile(r"<a\s+href=", re.DOTALL)


def _items_del_menu() -> str:
    m = _NAV.search(_SIDEBAR)
    assert m, "no se encontro el <nav> del menu principal"
    return m.group(0)


def test_todos_los_items_del_menu_anuncian_si_son_el_actual():
    menu = _items_del_menu()
    items = len(_ITEMS.findall(menu))
    marcados = menu.count('aria-current="page"')
    assert items >= 8, f"solo se encontraron {items} items de menu"
    assert marcados == items, (
        f"{items} items de menu pero {marcados} con aria-current: los que faltan "
        "no le dicen al lector en que pantalla esta el usuario"
    )


def test_el_aria_current_es_condicional():
    """Fijo en «page» seria peor que no tenerlo: los ocho serian el actual."""
    assert re.search(r"\{% if .*? %\}aria-current=\"page\" \{% endif %\}", _SIDEBAR)


def test_aria_current_no_se_emite_como_false():
    """La especificacion pide omitir el atributo, no ponerlo en «false»."""
    assert 'aria-current="false"' not in _SIDEBAR


class TestMenuCerrado:
    def test_se_vuelve_inerte(self):
        assert "setAttribute('inert'" in _BASE

    def test_el_estado_se_sincroniza_en_vez_de_toglearse(self):
        """Depende de dos cosas: si esta abierto Y si estamos en movil. En
        escritorio el menu siempre se ve y nunca debe quedar inerte."""
        assert "syncSidebarInert" in _BASE
        assert "matchMedia('(max-width: 767px)')" in _BASE
        # al abrir, al cerrar, al cargar y al cambiar el breakpoint
        assert _BASE.count("syncSidebarInert") >= 5

    def test_el_foco_vuelve_al_boton_que_lo_abrio(self):
        """Sin esto el foco queda en un elemento recien inertizado y el
        navegador lo manda al <body>, o sea al principio de la pagina."""
        assert "sidebar-open-btn" in _BASE
        cerrar = _BASE[_BASE.index("function closeSidebar"):]
        assert ".focus()" in cerrar[:900]
