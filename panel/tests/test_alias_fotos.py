"""El nombre del portal de origen no puede aparecer en una URL de foto.

La ficha pública se presenta como una publicación de Onnix SA. Las fotos
se servían desde `/images/remax/143028064-85/1.webp`: el nombre estaba en el
HTML, en la URL de cada imagen y en el menú contextual del navegador.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.utils.fotos import ALIAS_POR_FUENTE, alias_de_fuente, url_foto, urls_fotos


# Las cuatro fuentes con fotos en disco. Nombradas una por una y no derivadas de
# ALIAS_POR_FUENTE: un test que se parametriza sobre la misma lista que prueba
# no puede ver que alguien saque un elemento — borra el caso.
_FUENTES = ("onnixpy", "remax", "coldwell", "psir")

_RAIZ = Path(__file__).resolve().parent.parent.parent


class TestNingunNombreDePortalEnLaURL:
    @pytest.mark.parametrize("fuente", _FUENTES)
    def test_la_url_no_contiene_el_nombre_de_la_fuente(self, fuente):
        url = url_foto(fuente, "12345")
        assert url is not None
        assert fuente not in url, f"la URL delata la fuente: {url}"

    def test_las_cuatro_fuentes_tienen_alias(self):
        for fuente in _FUENTES:
            assert alias_de_fuente(fuente), f"{fuente} sin alias"

    def test_los_alias_no_se_repiten(self):
        """Dos fuentes con el mismo alias sirven la foto equivocada."""
        alias = list(ALIAS_POR_FUENTE.values())
        assert len(alias) == len(set(alias))

    def test_ningun_alias_contiene_el_nombre_de_su_fuente(self):
        for fuente, alias in ALIAS_POR_FUENTE.items():
            assert alias not in fuente
            assert fuente[:4] not in alias

    def test_una_fuente_desconocida_no_arma_url(self):
        """Mejor el placeholder que una imagen rota: sin `location` en nginx
        el path daría 404."""
        assert url_foto("portal-nuevo", "123") is None
        assert url_foto(None, "123") is None
        assert url_foto("remax", None) is None


class TestFormaDeLaURL:
    def test_la_foto_uno_es_la_primera(self):
        assert url_foto("remax", "abc").endswith("/1.webp")

    def test_pide_la_enesima(self):
        assert url_foto("remax", "abc", 7).endswith("/7.webp")

    def test_la_lista_va_de_uno_a_n(self):
        urls = urls_fotos("psir", "77", 3)
        assert len(urls) == 3
        assert urls[0].endswith("/1.webp")
        assert urls[-1].endswith("/3.webp")

    def test_sin_fotos_lista_vacia(self):
        assert urls_fotos("psir", "77", 0) == []


class TestUnSoloArmador:
    """El path estaba escrito seis veces. Que no vuelva."""

    _EXENTOS = {
        "panel/app/utils/fotos.py",          # el armador
        "panel/tests/test_alias_fotos.py",   # este archivo
        # Monta `/images/{alias}`, no arma una URL: el alias sale de
        # ALIAS_POR_FUENTE y no del nombre de la fuente. Lo cubre
        # TestLaPuertaDeAtras, que exige exactamente eso.
        "panel/app/main.py",
    }

    def test_nadie_arma_el_path_a_mano(self):
        sospechosos = []
        for patron in ("panel/app/**/*.py", "panel/app/**/*.html"):
            for archivo in _RAIZ.glob(patron):
                rel = archivo.relative_to(_RAIZ).as_posix()
                if rel in self._EXENTOS:
                    continue
                texto = archivo.read_text(encoding="utf-8", errors="ignore")
                # Sólo la construcción: un `/images/` suelto en un comentario o
                # en una ruta de nginx no arma nada.
                if re.search(r'["\']/images/["\']?\s*~|f["\']/images/\{', texto):
                    sospechosos.append(rel)
        assert not sospechosos, (
            "arman el path de la foto a mano en vez de usar url_foto(): "
            f"{sospechosos}"
        )


class TestElScriptDeLaLandingNoDiverge:
    """`build_destacadas.py` corre en el host y no puede importar de `panel/`.

    Su alias está duplicado a la fuerza. Esto es lo que impide que se separen.
    """

    def test_el_alias_del_script_es_el_mismo_que_el_de_la_app(self):
        script = (_RAIZ / "scripts/build_destacadas.py").read_text()
        m = re.search(r'^ALIAS_Onnix = "([^"]+)"', script, re.M)
        assert m, "build_destacadas.py ya no define ALIAS_Onnix"
        assert m.group(1) == ALIAS_POR_FUENTE["onnixpy"], (
            "el alias de la landing y el de la app dejaron de coincidir: "
            f"landing={m.group(1)} app={ALIAS_POR_FUENTE['onnixpy']}"
        )

    def test_el_script_no_escribe_el_nombre_del_portal(self):
        script = (_RAIZ / "scripts/build_destacadas.py").read_text()
        sin_comentarios = "\n".join(
            l for l in script.splitlines() if not l.lstrip().startswith("#")
        )
        assert "/images/onnixpy/" not in sin_comentarios


class TestLaPuertaDeAtras:
    """La app no puede servir `/images/<fuente>/...`.

    Sacar el `location /images/` de nginx no alcanzaba: la request que nginx no
    matchea cae al `location /`, llega al proxy y la resuelve el mount de
    Starlette. Se vio en la prueba negativa contra producción, con nginx ya
    recargado: `/images/remax/143028064-85/1.webp` seguía dando 200.
    """

    def test_main_no_monta_el_arbol_entero(self):
        main = (_RAIZ / "panel/app/main.py").read_text()
        sin_comentarios = "\n".join(
            l for l in main.splitlines() if not l.lstrip().startswith("#")
        )
        assert 'app.mount("/images"' not in sin_comentarios, (
            "montar /images sobre el arbol por fuente deja servible "
            "/images/remax/... por el proxy"
        )

    def test_main_monta_por_alias(self):
        main = (_RAIZ / "panel/app/main.py").read_text()
        assert "ALIAS_POR_FUENTE" in main, (
            "el mount tiene que derivar del mismo mapa que arma las URLs"
        )


class TestNginxSirveLosAlias:
    """El otro lado del arreglo. Sin estos `location`, la URL nueva da 404."""

    _CONFIGS = ("nginx_prod_v7.conf", "nginx_staging.conf")

    @pytest.mark.parametrize("config", _CONFIGS)
    def test_hay_un_location_por_alias(self, config):
        texto = (_RAIZ / config).read_text()
        for fuente, alias in ALIAS_POR_FUENTE.items():
            assert f"location ^~ /images/{alias}/" in texto, (
                f"{config}: falta el location de {alias} ({fuente})"
            )

    @pytest.mark.parametrize("config", _CONFIGS)
    def test_cada_alias_apunta_a_su_directorio(self, config):
        texto = (_RAIZ / config).read_text()
        for fuente, alias in ALIAS_POR_FUENTE.items():
            bloque = texto.split(f"location ^~ /images/{alias}/")[1].split("}")[0]
            assert f"alias /home/onnix/images/{fuente}/;" in bloque, (
                f"{config}: {alias} no apunta a {fuente}"
            )

    @pytest.mark.parametrize("config", _CONFIGS)
    def test_no_queda_el_location_general(self, config):
        """Con él, `/images/remax/...` sigue resolviendo."""
        texto = (_RAIZ / config).read_text()
        sin_comentarios = "\n".join(
            l for l in texto.splitlines() if not l.lstrip().startswith("#")
        )
        assert "location /images/ {" not in sin_comentarios
