"""Carril K — la pantalla de login.

Tres defectos verificados en el audit, todos en la misma pantalla:

1. El boton hacia `this.form.submit()`, que **saltea la validacion del
   navegador**. Un envio vacio llegaba al server y `auth.py` le sumaba una
   marca al contador de bloqueo por una tecla mal apretada. Ademas el
   `onclick` no corre cuando el usuario aprieta Enter: dos caminos distintos
   para el mismo acto.
2. Faltaba `autocomplete` en los dos campos, asi que ningun gestor de
   contrasenas ofrecia completar.
3. El route pasaba `is_locked` y el template no lo leia nunca. La cuenta
   bloqueada se distingue por el texto del error, no por una variable muerta.
"""
from __future__ import annotations

import re
from pathlib import Path

_PANEL = Path(__file__).resolve().parent.parent
_LOGIN = (_PANEL / "app" / "templates" / "login.html").read_text(encoding="utf-8")
_AUTH = (_PANEL / "app" / "routes" / "auth.py").read_text(encoding="utf-8")

# Los comentarios Jinja citan el patron que estos tests prohiben. Sin sacarlos,
# el test falla contra su propia documentacion — el mismo error que se arreglo
# en el chequeo de leads (3463464).
_LOGIN_CODIGO = re.sub(r"\{#.*?#\}", "", _LOGIN, flags=re.DOTALL)


def test_el_envio_no_saltea_la_validacion_del_navegador():
    """`form.submit()` ignora `required`; `requestSubmit()` y el submit nativo no."""
    assert ".submit()" not in _LOGIN_CODIGO, (
        "form.submit() saltea la validacion HTML5: un envio vacio llega al "
        "server y suma una marca al contador de bloqueo de auth.py"
    )


def test_el_feedback_de_envio_cuelga_del_form_no_del_boton():
    """Colgado del boton, apretar Enter no lo dispara. En el form, siempre."""
    assert "onclick=" not in _LOGIN_CODIGO
    assert re.search(r"<form[^>]*\son(submit|:submit)", _LOGIN_CODIGO), (
        "el spinner tiene que dispararse en el submit del form, que corre "
        "despues de la validacion y tambien cuando se aprieta Enter"
    )


def test_los_campos_declaran_autocomplete():
    assert 'autocomplete="username"' in _LOGIN
    assert 'autocomplete="current-password"' in _LOGIN


def test_el_error_se_anuncia():
    assert 'role="alert"' in _LOGIN


def test_el_route_no_pasa_una_variable_que_nadie_lee():
    assert "is_locked" not in _LOGIN_CODIGO
    assert '"is_locked"' not in _AUTH, (
        "el template nunca la leyo; la cuenta bloqueada ya se distingue por "
        "el texto del error"
    )
