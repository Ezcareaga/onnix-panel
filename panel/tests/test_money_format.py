"""Un solo formato de número — app/utils/money.py.

El bug que cierra: en la misma pantalla de propiedades convivían `1.234`,
`USD 250,000` y `US$ 250.000`. El asesor copiaba al portapapeles el del medio
y se lo mandaba por WhatsApp a un cliente que abría la página pública con el
tercero. En Paraguay el separador de miles es el punto.
"""

import ast
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.money import miles, precio


class TestMiles:
    def test_separador_es_punto(self):
        assert miles(250000) == "250.000"

    def test_millones(self):
        assert miles(1500000000) == "1.500.000.000"

    def test_sin_separador_bajo_mil(self):
        assert miles(213) == "213"

    def test_decimal_de_la_base(self):
        assert miles(Decimal("120000")) == "120.000"

    def test_string_numerico_del_querystring(self):
        # Los chips de filtro reciben el valor crudo del querystring.
        assert miles("250000") == "250.000"

    def test_redondea_a_entero(self):
        assert miles(Decimal("250000.6")) == "250.001"

    def test_lo_que_no_es_numero_vuelve_tal_cual(self):
        assert miles("cualquier cosa") == "cualquier cosa"


class TestPrecio:
    def test_usd_primero(self):
        assert precio(Decimal("250000"), Decimal("1800000000")) == "USD 250.000"

    def test_pyg_de_fallback(self):
        assert precio(None, Decimal("1800000000")) == "₲ 1.800.000.000"

    def test_sin_precio_cae_en_vacio(self):
        assert precio(None, None) == "A consultar"

    def test_texto_del_vacio_configurable(self):
        # El portal público le habla al cliente, el panel al asesor.
        assert precio(None, None, vacio="Consultar precio") == "Consultar precio"

    @pytest.mark.parametrize("cero", [0, Decimal("0"), None])
    def test_cero_no_es_un_precio(self, cero):
        # LOW-2: un precio en 0 nunca se muestra como "USD 0".
        assert precio(cero, cero) == "A consultar"

    def test_ningun_formato_usa_la_coma(self):
        # La coma es el separador que rompía el par panel↔portal.
        assert "," not in precio(Decimal("1200000"), Decimal("8880000000"))
        assert "," not in precio(None, Decimal("8880000000"))


# ─────────────────────────────────────────────────────────────────────────────
# Los templates
# ─────────────────────────────────────────────────────────────────────────────
#
# `miles` y `precio` existen desde el carril J, pero dieciséis expresiones
# seguían formateando a mano y la pantalla mostraba la coma anglosajona. El
# escaneo va sobre TODOS los templates y no sobre una lista escrita a mano: un
# archivo nuevo que formatee a mano entra solo al test, no cuando alguien se
# acuerde de agregarlo.

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# El separador de miles de Python dentro de una expresión Jinja. Es lo único
# prohibido: `"%.2f"|format(costo)` es un decimal, no un separador, y se queda.
_SEPARADOR_PY = ":" + ","

_COMENTARIO_JINJA = re.compile(r"\{#.*?#\}", re.S)
_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.S)
_EXPRESION_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def _expresiones(path: Path) -> list[str]:
    """Las expresiones Jinja del template, sin los comentarios.

    La trampa del repo: el comentario que explica el patrón prohibido lo
    contiene. Se filtran comentarios Jinja y HTML antes de mirar nada.
    """
    fuente = _COMENTARIO_HTML.sub("", _COMENTARIO_JINJA.sub("", path.read_text()))
    return _EXPRESION_JINJA.findall(fuente)


def _todos_los_templates() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


class TestTemplatesSinComa:
    @pytest.mark.parametrize(
        "template", _todos_los_templates(), ids=lambda p: p.name
    )
    def test_ninguna_expresion_formatea_miles_a_mano(self, template: Path):
        culpables = [e for e in _expresiones(template) if _SEPARADOR_PY in e]
        assert not culpables, (
            f"{template.relative_to(_TEMPLATES)} formatea miles a mano: "
            f"{culpables} — usar miles() o precio()."
        )

    def test_el_escaneo_mira_los_cuatro_archivos_del_bug(self):
        # Un test parametrizado sobre una lista no puede ver que la lista se
        # achicó: si el glob deja de encontrar estos archivos, borra los casos
        # y queda verde. Los cuatro del bug se nombran acá, uno por uno.
        nombres = {p.relative_to(_TEMPLATES).as_posix() for p in _todos_los_templates()}
        assert {
            "contacts_detail.html",
            "partials/ai_metrics_detail.html",
            "partials/bot_health_stats.html",
            "partials/lead_item.html",
        } <= nombres

    def test_el_escaneo_ve_el_patron_cuando_esta(self, tmp_path):
        # Sin esto, un regex roto deja todos los templates verdes para siempre.
        sucio = tmp_path / "sucio.html"
        sucio.write_text(
            "{# " + _SEPARADOR_PY + " #}"
            '<p>{{ "{' + _SEPARADOR_PY + '}".format(n) }}</p>'
        )
        assert [e for e in _expresiones(sucio) if _SEPARADOR_PY in e]

    def test_el_escaneo_ignora_el_patron_en_un_comentario(self, tmp_path):
        limpio = tmp_path / "limpio.html"
        limpio.write_text(
            "{# antes decía " + _SEPARADOR_PY + " #}"
            "<!-- y acá también: " + _SEPARADOR_PY + " -->"
            "<p>{{ miles(n) }}</p>"
        )
        assert not [e for e in _expresiones(limpio) if _SEPARADOR_PY in e]


class TestNumeroRenderizado:
    """El escaneo dice que la coma no está en la fuente; esto, que sale punto.

    El escaneo mira el código; el número que ve la administradora sale del render.
    """

    def _celdas(self, html: str) -> list[str]:
        return [
            re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<td\b.*?</td>", html, re.S)
        ]

    def test_los_tokens_salen_con_punto(self):
        from app.tz import get_templates

        html = get_templates().env.get_template(
            "partials/ai_metrics_detail.html"
        ).render(
            days=7,
            tokens_by_day=[
                SimpleNamespace(
                    date="2026-08-22",
                    messages=9,
                    tokens_in=1234567,
                    tokens_out=89012,
                )
            ],
            avg_latency_ms=0,
            cost_estimate=SimpleNamespace(total_usd=0.0, per_model={}),
            total_tokens_in=1234567,
            total_tokens_out=89012,
            total_messages=9,
        )
        celdas = self._celdas(html)
        # Igualdad y no substring: "1.234" está contenido en "1.234.567".
        assert "1.234.567" in celdas
        assert "89.012" in celdas
        assert "1.323.579" in celdas  # la suma de la fila
        assert not [c for c in celdas if "," in c]

    def test_el_precio_de_la_ficha_sale_con_punto(self):
        assert precio(1234567) == "USD 1.234.567"


# ─────────────────────────────────────────────────────────────────────────────
# El código Python
# ─────────────────────────────────────────────────────────────────────────────
#
# El escaneo de templates dejó afuera la mitad del problema. En Python quedaban
# veintitrés sitios formateando a mano, y nueve escribían la coma de verdad:
# `template_service.py` la metía en la variable de una plantilla de WhatsApp que
# sale hacia un cliente real, y `lead_export_service.py` en el export que el
# asesor manda. Los otros catorce ya ponían el punto — con la misma línea
# copiada catorce veces.
#
# Se mira el AST y no el texto: así los comentarios no cuentan (no existen en el
# árbol), y un `(?:,` adentro de una expresión regular tampoco — el literal solo
# cuenta si la coma está dentro de una llave de formato.

_APP = Path(__file__).resolve().parent.parent / "app"

# El único lugar donde el separador de Python está permitido: es la función que
# lo convierte en punto.
_DONDE_SE_PERMITE = {"utils/money.py"}

_PLACEHOLDER_CON_COMA = re.compile(r"\{[^{}]*:[^{}]*,[^{}]*\}")


def _plantillas_de_format(arbol: ast.AST) -> list[ast.Constant]:
    """Los literales que son el receptor de un `.format(...)`.

    Un string cualquiera con llaves, dos puntos y una coma adentro puede ser
    JSON, una expresión regular o un mensaje: mirarlos a todos dio 23 falsos
    positivos en el primer intento. Solo es una plantilla de formato el que
    alguien efectivamente formatea.
    """
    plantillas: list[ast.Constant] = []
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "format"
            and isinstance(nodo.func.value, ast.Constant)
            and isinstance(nodo.func.value.value, str)
        ):
            plantillas.append(nodo.func.value)
    return plantillas


def _formatos_con_coma(path: Path) -> list[str]:
    """Los sitios de `path` que formatean miles con la coma de Python."""
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    hallazgos: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FormattedValue) and nodo.format_spec is not None:
            spec = "".join(
                v.value
                for v in nodo.format_spec.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if "," in spec:
                hallazgos.append(f"línea {nodo.lineno}: f-string con formato {spec!r}")
    for plantilla in _plantillas_de_format(arbol):
        if _PLACEHOLDER_CON_COMA.search(plantilla.value):
            hallazgos.append(
                f"línea {plantilla.lineno}: "
                f"plantilla {plantilla.value[:40]!r}.format(…)"
            )
    return hallazgos


def _todo_el_codigo() -> list[Path]:
    return sorted(
        p for p in _APP.rglob("*.py")
        if p.relative_to(_APP).as_posix() not in _DONDE_SE_PERMITE
    )


class TestPythonSinComa:
    @pytest.mark.parametrize(
        "modulo", _todo_el_codigo(), ids=lambda p: p.relative_to(_APP).as_posix()
    )
    def test_ningun_modulo_formatea_miles_a_mano(self, modulo: Path):
        culpables = _formatos_con_coma(modulo)
        assert not culpables, (
            f"{modulo.relative_to(_APP)} formatea miles a mano:\n  "
            + "\n  ".join(culpables)
            + "\nUsar miles() o precio() de app.utils.money."
        )

    def test_el_escaneo_mira_los_modulos_del_bug(self):
        # Parametrizar sobre un glob no puede ver que el glob se vació: borra
        # los casos y queda verde. Los que tenían la coma se nombran acá.
        vistos = {p.relative_to(_APP).as_posix() for p in _todo_el_codigo()}
        assert {
            "services/template_service.py",
            "services/lead_export_service.py",
            "bot/handlers/url_detection.py",
            "bot/search/relaxation.py",
            "bot/search/alternatives.py",
            "bot/core/response_builder.py",
            "bot/services/infocasas/infocasas_service.py",
        } <= vistos

    def test_el_escaneo_ve_el_patron_cuando_esta(self, tmp_path):
        sucio = tmp_path / "sucio.py"
        sucio.write_text(
            '"""Docstring que menciona ' + _SEPARADOR_PY + ' y no cuenta."""\n'
            "# Un comentario con " + _SEPARADOR_PY + " tampoco cuenta.\n"
            'def f(n):\n    return f"USD {n' + _SEPARADOR_PY + '}"\n',
            encoding="utf-8",
        )
        assert len(_formatos_con_coma(sucio)) == 1

    def test_ve_la_plantilla_de_format(self, tmp_path):
        # La otra forma de escribir el bug: el literal y el `.format()` aparte.
        sucio = tmp_path / "format.py"
        sucio.write_text(
            'def f(n):\n    return "{' + _SEPARADOR_PY + '.0f}".format(n)\n',
            encoding="utf-8",
        )
        assert len(_formatos_con_coma(sucio)) == 1

    @pytest.mark.parametrize(
        "linea,que_es",
        [
            (
                'RE = re.compile(r"zona(?P<z>[^,]+(?'
                + _SEPARADOR_PY
                + r'\s*[A-Z])?)")',
                "expresión regular — el falso positivo de lead_parser.py",
            ),
            (
                'PAYLOAD = \'{"tipo"' + _SEPARADOR_PY + ' "casa", "n": 2}\'',
                "un JSON escrito a mano",
            ),
            (
                'MENSAJE = "Opciones {a' + _SEPARADOR_PY + ' b} sin formatear"',
                "un texto que nadie formatea",
            ),
        ],
    )
    def test_lo_que_tiene_el_patron_y_no_formatea_nada(self, tmp_path, linea, que_es):
        """El primer intento del escaneo miró todos los literales: 23 rojos.

        Los tres casos de acá son los que lo rompían. Un string con el patrón
        adentro no formatea nada hasta que alguien lo formatea.
        """
        limpio = tmp_path / "limpio.py"
        limpio.write_text("import re\n" + linea + "\n", encoding="utf-8")
        assert _formatos_con_coma(limpio) == [], que_es

    def test_money_es_el_unico_que_puede(self):
        """Si el permiso se amplía, que se vea en el diff de este test."""
        assert _DONDE_SE_PERMITE == {"utils/money.py"}
        assert _formatos_con_coma(_APP / "utils" / "money.py")
