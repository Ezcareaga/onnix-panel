"""scripts/build_destacadas.py — el bloque de destacadas que se genera en el deploy.

Solo la parte pura: render, empalme, filtro de disco y formato. La consulta a
Postgres no se testea acá porque no hay Postgres en el host del test y montar
uno para verificar un `docker exec` sería testear a docker.

Lo que sí se pinnea son las dos copias que este script tiene del portal — el
formato de precio y las etiquetas de tipo — porque viven duplicadas a propósito
(el script corre en el host, sin SQLAlchemy) y una copia que nadie compara se
desincroniza sola.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LANDING = REPO / "landing" / "index.html"

_spec = importlib.util.spec_from_file_location(
    "build_destacadas", REPO / "scripts" / "build_destacadas.py"
)
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)


def _prop(**over):
    prop = {
        "id": 42, "external_id": "51555", "city": "Asuncion",
        "neighborhood": "Villa Morra", "property_type": "departamento",
        "bedrooms": 1, "bathrooms": 1, "total_area_m2": 55.47,
        "price_usd": 146365.00,
    }
    prop.update(over)
    return prop


# ---------------------------------------------------------------------------
# Empalme
# ---------------------------------------------------------------------------

def _documento(medio: str = "<p>lo que había antes</p>") -> str:
    return f"<section>{bd.INICIO}{medio}{bd.FIN}</section>"


def test_empalmar_reemplaza_solo_lo_que_hay_entre_los_marcadores():
    salida = bd.empalmar(_documento(), "NUEVO")
    assert salida == f"<section>{bd.INICIO}NUEVO{bd.FIN}</section>"
    assert "lo que había antes" not in salida


def test_empalmar_es_idempotente():
    """Correrlo dos veces tiene que dar el mismo archivo, o cada deploy acumula."""
    una = bd.empalmar(_documento(), "NUEVO")
    dos = bd.empalmar(una, "NUEVO")
    assert una == dos


def test_empalmar_sin_marcadores_falla_ruidoso():
    with pytest.raises(ValueError, match="marcadores"):
        bd.empalmar("<section>sin marcadores</section>", "NUEVO")


def test_empalmar_con_los_marcadores_al_reves_falla():
    invertido = f"<section>{bd.FIN}algo{bd.INICIO}</section>"
    with pytest.raises(ValueError):
        bd.empalmar(invertido, "NUEVO")


def test_la_landing_del_repo_tiene_los_marcadores():
    """Sin esto el script no tiene dónde escribir y el deploy sale ruidoso."""
    assert LANDING.is_file(), f"falta {LANDING}: este test corre desde el repo"
    documento = LANDING.read_text(encoding="utf-8")
    assert bd.INICIO in documento
    assert bd.FIN in documento
    # Y el empalme real tiene que funcionar sobre el archivo real, no solo
    # sobre un documento de juguete.
    bd.empalmar(documento, bd.render([_prop()]))


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------

def test_la_ficha_linkea_al_id_y_deja_el_slug_al_servidor():
    """/p/{id} redirige 301 al canónico: una sola copia de slugify, la del panel."""
    assert 'href="/p/42"' in bd.tarjeta(_prop())


def test_la_ficha_muestra_precio_tipo_lugar_y_specs():
    ficha = bd.tarjeta(_prop())
    assert "USD 146.365" in ficha
    assert "Departamento en Villa Morra" in ficha
    assert "1 dormitorio · 1 baño · 55 m²" in ficha


def test_las_specs_que_faltan_no_dejan_hueco():
    ficha = bd.tarjeta(_prop(bedrooms=None, bathrooms=None, total_area_m2=360))
    assert "360 m²" in ficha
    assert "dormitorio" not in ficha
    assert "None" not in ficha


def test_sin_ninguna_spec_no_se_emite_la_linea():
    ficha = bd.tarjeta(_prop(bedrooms=None, bathrooms=None, total_area_m2=None))
    assert "destacada-specs" not in ficha


def test_el_plural_de_las_specs():
    ficha = bd.tarjeta(_prop(bedrooms=3, bathrooms=2))
    assert "3 dormitorios · 2 baños" in ficha


def test_el_texto_de_la_base_se_escapa():
    """Los títulos vienen de un scraper. Nada de la base entra crudo al HTML."""
    ficha = bd.tarjeta(_prop(neighborhood='<script>alert("x")</script>'))
    assert "<script>" not in ficha
    assert "&lt;script&gt;" in ficha


def test_sin_barrio_cae_a_la_ciudad():
    assert "Casa en Altos" in bd.tarjeta(_prop(neighborhood=None, city="Altos",
                                               property_type="casa"))


def test_la_foto_no_lleva_alt_porque_el_link_ya_se_llama():
    """El <a> tiene aria-label con tipo, lugar y precio: un alt repetiría."""
    ficha = bd.tarjeta(_prop())
    assert 'alt=""' in ficha
    assert 'aria-label="Departamento en Villa Morra — USD 146.365"' in ficha


# ---------------------------------------------------------------------------
# Filtro de disco
# ---------------------------------------------------------------------------

def test_se_cae_la_que_no_tiene_la_foto_en_el_disco(tmp_path):
    """local_image_count es un contador de la base y ya divergió del disco."""
    (tmp_path / "onnixpy" / "111").mkdir(parents=True)
    (tmp_path / "onnixpy" / "111" / "1.webp").write_bytes(b"x")
    quedan = bd.con_foto_en_disco(
        [_prop(external_id="111"), _prop(external_id="222")], tmp_path
    )
    assert [p["external_id"] for p in quedan] == ["111"]


def test_sin_directorio_de_imagenes_no_queda_ninguna(tmp_path):
    assert bd.con_foto_en_disco([_prop()], tmp_path / "no-existe") == []


# ---------------------------------------------------------------------------
# Las dos copias del portal, pinneadas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [8000, 146365, 1000, 20000000, 99204.55])
def test_el_precio_se_formatea_igual_que_en_el_portal(valor):
    from app.services.public_property_service import format_price_display
    assert bd.precio(valor) == format_price_display(valor, None)


def test_las_etiquetas_de_tipo_no_se_separaron_del_portal():
    from app.services.public_property_service import PORTAL_TIPO_OPTIONS
    portal = dict(PORTAL_TIPO_OPTIONS)
    distintas = {k: (v, portal.get(k)) for k, v in bd.ETIQUETAS_TIPO.items()
                 if portal.get(k) != v}
    assert not distintas, (
        "las etiquetas del script se separaron de PORTAL_TIPO_OPTIONS: "
        f"{distintas}"
    )


def test_los_limites_de_precio_son_los_del_portal():
    from app.services.public_property_service import _PRICE_BOUNDS_USD
    lo, hi = _PRICE_BOUNDS_USD["venta"]
    assert (int(lo), int(hi)) == (bd.PRECIO_MIN_USD, bd.PRECIO_MAX_USD)


def test_la_consulta_pide_los_tipos_que_el_script_sabe_etiquetar():
    """Si alguien agrega un tipo al dict, la consulta lo trae; si lo saca, no."""
    tipos_sql = bd.CONSULTA.format(
        minimo=bd.PRECIO_MIN_USD, maximo=bd.PRECIO_MAX_USD,
        tipos=", ".join(f"'{t}'" for t in sorted(bd.ETIQUETAS_TIPO)),
    )
    for tipo in bd.ETIQUETAS_TIPO:
        assert f"'{tipo}'" in tipos_sql
