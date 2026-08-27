"""Unit tests for PublicPropertyService.get_portal_listing (M6.4b)."""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.services.public_property_service import (
    PublicPropertyService,
    format_price_display,
    public_price_display,
)


def _portal_row(**overrides) -> dict:
    row = {
        "id": 42,
        "source": "onnixpy",
        "external_id": "Onnix-123",
        "title": "Casa en Asunción",
        "price_usd": Decimal("120000"),
        "price_pyg": Decimal("888000000"),
        "price_currency": "USD",
        "city": "Asuncion",
        "neighborhood": None,
        "operation": "venta",
        "property_type": "casa",
        "bedrooms": 3,
        "bathrooms": 2,
        "total_area_m2": Decimal("250"),
        "is_active": True,
        "on_hold": False,
        "local_image_count": 5,
    }
    row.update(overrides)
    return row


class TestFormatPriceDisplay:
    def test_usd(self):
        assert format_price_display(Decimal("120000"), Decimal("888000000")) == "USD 120.000"

    def test_pyg_when_no_usd(self):
        assert format_price_display(None, Decimal("305000000")) == "₲ 305.000.000"

    def test_consultar_when_both_missing(self):
        assert format_price_display(None, None) == "Consultar precio"


class TestPublicPriceDisplay:
    """M6.4b outlier fix — implausible prices show 'Consultar precio' publicly."""

    def test_venta_outlier_high_masked(self):
        assert public_price_display(Decimal("300000000"), Decimal("1"), "venta") == "Consultar precio"

    def test_venta_plausible_formats(self):
        assert public_price_display(Decimal("120000"), Decimal("888000000"), "venta") == "USD 120.000"

    def test_alquiler_outlier_high_masked(self):
        assert public_price_display(Decimal("60000"), Decimal("1"), "alquiler") == "Consultar precio"

    def test_alquiler_plausible_low_formats(self):
        assert public_price_display(Decimal("213"), Decimal("1500000"), "alquiler") == "USD 213"

    def test_venta_below_floor_masked(self):
        assert public_price_display(Decimal("999"), Decimal("7000000"), "venta") == "Consultar precio"

    def test_operation_none_plausible_formats(self):
        assert public_price_display(Decimal("150000"), Decimal("1"), None) == "USD 150.000"

    def test_no_usd_falls_back_to_pyg(self):
        assert public_price_display(None, Decimal("305000000"), "venta") == "₲ 305.000.000"


class TestGetPortalListing:
    async def test_card_masks_outlier_price(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[
                _portal_row(price_usd=Decimal("300000000"), operation="venta")
            ]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            result = await PublicPropertyService.get_portal_listing(AsyncMock(), page=1)
        assert result["cards"][0]["price_display"] == "Consultar precio"

    async def test_forces_onnixpy_active_filters(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[_portal_row()]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            await PublicPropertyService.get_portal_listing(AsyncMock(), page=1)
        filters = mock_list.call_args.args[1]
        assert filters.source == "onnixpy"
        assert filters.state == "active"
        assert filters.search_text is None
        assert filters.amenities is None
        assert filters.barato is False

    async def test_portal_pagination_24_per_page(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=100),
        ):
            result = await PublicPropertyService.get_portal_listing(AsyncMock(), page=3)
        assert mock_list.call_args.kwargs["limit"] == 24
        assert mock_list.call_args.kwargs["offset"] == 48
        assert result["total"] == 100
        assert result["total_pages"] == 5  # ceil(100/24)
        assert result["page"] == 3

    async def test_card_enrichment(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[_portal_row()]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            result = await PublicPropertyService.get_portal_listing(AsyncMock(), page=1)
        card = result["cards"][0]
        assert card["public_path"] == "/prop/42-casa-en-asuncion-asuncion"
        # El alias, no el nombre del portal. Literal y no `url_foto(...)`:
        # un assert que llama al armador que prueba da verde con cualquier
        # cosa que el armador devuelva.
        assert card["photo_url"] == "/images/p3/Onnix-123/1.webp"
        assert "onnixpy" not in card["photo_url"]
        assert card["price_display"] == "USD 120.000"
        assert card["tipo_label"] == "Casa"

    async def test_card_photo_none_when_no_local_images(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[_portal_row(local_image_count=0)]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            result = await PublicPropertyService.get_portal_listing(AsyncMock(), page=1)
        assert result["cards"][0]["photo_url"] is None

    async def test_portal_lists_only_onnixpy_active(self):
        """[spec test] El service fuerza source=onnixpy + state=active siempre."""
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=0),
        ) as mock_count:
            await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1, tipo="casa", ciudad="Luque"
            )
        for mock in (mock_list, mock_count):
            filters = mock.call_args.args[1]
            assert filters.source == "onnixpy"
            assert filters.state == "active"

    async def test_portal_excludes_ic(self):
        """[spec test] source es inmutable onnixpy — infocasas imposible.

        get_portal_listing no acepta source en su firma; este test documenta
        que ni siquiera un caller malicioso puede inyectarlo.
        """
        import inspect
        sig = inspect.signature(PublicPropertyService.get_portal_listing)
        assert "source" not in sig.parameters
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=0),
        ):
            await PublicPropertyService.get_portal_listing(AsyncMock(), page=1)
        assert mock_list.call_args.args[1].source != "infocasas"
        assert mock_list.call_args.args[1].source == "onnixpy"

    async def test_portal_filter_by_tipo(self):
        """[spec test] tipo → PropertyFilters.property_type."""
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=0),
        ):
            await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1, tipo="departamento-en-pozo"
            )
        assert mock_list.call_args.args[1].property_type == "departamento-en-pozo"

    async def test_portal_filter_by_precio_rango(self):
        """[spec test] precio_min/max → PropertyFilters.price_min/max (USD)."""
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=0),
        ):
            await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1,
                precio_min=Decimal("50000"), precio_max=Decimal("150000"),
            )
        filters = mock_list.call_args.args[1]
        assert filters.price_min == Decimal("50000")
        assert filters.price_max == Decimal("150000")

    async def test_optional_filters_passthrough(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=0),
        ):
            await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1, tipo="departamento", ciudad="Luque",
                operacion="alquiler", precio_min=Decimal("50000"),
                precio_max=Decimal("100000"),
            )
        filters = mock_list.call_args.args[1]
        assert filters.property_type == "departamento"
        assert filters.city == "Luque"
        assert filters.operation == "alquiler"
        assert filters.price_min == Decimal("50000")
        assert filters.price_max == Decimal("100000")


# ---------------------------------------------------------------------------
# El portal colapsa el proyecto en una tarjeta
# ---------------------------------------------------------------------------


class TestColapsoDeProyectos:
    """407 títulos producían 2.112 de las 5.105 filas del portal — el 41,4 %.

    No son duplicados: `run_dedup_same_source` ya se llevó los que sí lo eran.
    Éstos difieren en precio o superficie y son unidades reales del mismo
    proyecto. El dato está bien; mostrarlas de a una estaba mal.
    """

    async def test_el_portal_pide_las_dos_consultas_colapsadas(self):
        """Si una colapsa y la otra no, el total no coincide con las tarjetas."""
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[_portal_row()]),
        ) as mock_list, patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ) as mock_count:
            await PublicPropertyService.get_portal_listing(AsyncMock(), page=1)

        assert mock_list.call_args.kwargs["colapsar_proyectos"] is True
        assert mock_count.call_args.kwargs["colapsar_proyectos"] is True

    async def test_una_unidad_sola_muestra_su_precio(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[
                _portal_row(unidades=1, precio_desde=Decimal("120000"))
            ]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            card = (await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1
            ))["cards"][0]

        assert card["unidades"] == 1
        assert card["price_display"] == "USD 120.000"

    async def test_un_proyecto_lleva_el_conteo_y_el_desde(self):
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[
                _portal_row(
                    price_usd=Decimal("180000"),
                    unidades=85,
                    precio_desde=Decimal("21600"),
                )
            ]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            card = (await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1
            ))["cards"][0]

        assert card["unidades"] == 85
        # El «desde» es el mínimo del grupo, no el precio de la fila elegida.
        assert card["precio_desde_display"] == "USD 21.600"
        assert card["price_display"] == "USD 180.000"

    async def test_el_desde_pasa_por_el_enmascarado_de_precios(self):
        """Un «desde» implausible no puede escaparse del filtro que la tarjeta
        suelta sí tiene."""
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[
                _portal_row(
                    unidades=12,
                    precio_desde=Decimal("300000000"),
                    operation="venta",
                )
            ]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            card = (await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1
            ))["cards"][0]

        assert card["precio_desde_display"] == "Consultar precio"

    async def test_una_fila_sin_las_columnas_nuevas_no_rompe(self):
        """`unidades` ausente = una unidad. La fila del panel no las trae."""
        with patch(
            "app.services.public_property_service.property_repo.list_with_filters",
            new=AsyncMock(return_value=[_portal_row()]),
        ), patch(
            "app.services.public_property_service.property_repo.count_with_filters",
            new=AsyncMock(return_value=1),
        ):
            card = (await PublicPropertyService.get_portal_listing(
                AsyncMock(), page=1
            ))["cards"][0]

        assert card["unidades"] == 1
