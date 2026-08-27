"""
Tests para la feature leads-action-buttons:
- ic_url expuesto en lead_repo._BASE_COLUMNS
- lead_item.html rediseñado con interacción por columna
"""
class TestIcUrlInRepo:
    """ic_url debe estar en _BASE_COLUMNS de lead_repo."""

    def test_ic_url_in_base_columns(self):
        """ip.url as ic_url debe estar en _BASE_COLUMNS."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "ic_url" in _BASE_COLUMNS

    def test_property_url_still_in_base_columns(self):
        """property_url no debe haberse eliminado al agregar ic_url."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "property_url" in _BASE_COLUMNS


class TestLeadRowTemplate:
    """Tests para lead_item.html (la fila que abajo de 768px es card)."""

    from pathlib import Path
    _TEMPLATE = Path(__file__).parent.parent / "app/templates/partials/lead_item.html"

    def _read(self):
        with open(self._TEMPLATE) as f:
            return f.read()

    def test_no_global_row_onclick(self):
        """El <tr> no debe tener onclick global (navegación al perfil)."""
        content = self._read()
        assert "event.target.closest('[x-data]')" not in content

    def test_per_column_onclick_contacts(self):
        """Teléfono y Fuente navegan al perfil por onclick.

        La columna Nombre dejó de hacerlo: ahora es un <a> de verdad, que es
        lo único que un teclado puede recorrer cuando la fila es una card.
        """
        content = self._read()
        assert "window.location='/contacts/" in content

    def test_prop_url_fallback_to_ic_url(self):
        """La columna Propiedad debe usar prop_url = property_url or ic_url."""
        content = self._read()
        assert "ic_url" in content
        assert "prop_url" in content

    def test_no_lead01_house_button(self):
        """El botón LEAD-01 (casa) debe haberse eliminado."""
        content = self._read()
        assert "LEAD-01" not in content
        # El SVG de casa (path d="M3 12l2-2m0 0l7-7") ya no debe estar
        assert "M3 12l2-2m0 0l7-7" not in content

    def test_action_buttons_44px_hitbox(self):
        """44×44 es el mínimo de ui.md, y esta fila se toca desde el celular.

        Estaban en w-10 h-10 (40px) — 4px cortos, invisibles en el código y
        no en el pulgar. 2 activos + 2 deshabilitados + el de asignar.
        """
        content = self._read()
        assert content.count("w-11 h-11") >= 4
        assert "w-10 h-10" not in content

    def test_disabled_buttons_pointer_events_none(self):
        """Los botones deshabilitados deben tener pointer-events-none."""
        content = self._read()
        assert "pointer-events-none" in content

    def test_prop_url_uses_anchor_not_window_open(self):
        """Propiedad usa <a href> en lugar de onclick window.open (evita XSS javascript: URI)."""
        content = self._read()
        assert "window.open('{{ prop_url }}" not in content
        assert 'href="{{ prop_url }}"' in content

    def test_name_cell_is_a_real_link(self):
        """El nombre navega con un <a>, no con un onclick sobre el <td>."""
        content = self._read()
        assert 'href="/contacts/{{ lead.id }}"' in content
