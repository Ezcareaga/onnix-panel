// TDD — política de estados HTTP de los handlers htmx de app.js.
//
// El bug que cubre: `htmx:beforeSwap` ponía `event.detail.isError = false`
// para TODO 4xx, y htmx deriva `successful` de ahí — la línea es
// `i.failed=f; i.successful=!f` en htmx-2.0.4.min.js. Con eso un 422 llegaba
// a `htmx:afterRequest` como éxito, y los templates que preguntan por
// `$event.detail.successful` cerraban el modal, reseteaban el form y tiraban
// un toast verde sin que el servidor hubiera guardado nada.
//
// Verificado en el minificado: `if(m.shouldSwap)` se evalúa aparte de
// `isError`, así que el cuerpo inline del 4xx swapea igual con isError=true.
// Y `if(f){ fe(o,"htmx:responseError", ...) }` dispara el toast solo cuando
// isError quedó en true. De ahí la política: isError se deja en true para no
// mentir, y el toast redundante se silencia SOLO en los estados que ya
// renderizaron el error dentro del formulario.
//
// Corre con `node --test "tests/js/**/*.test.mjs"` (wrapper: tests/test_js_suite.py).
import test from "node:test";
import assert from "node:assert/strict";

import HtmxErrorPolicy from "../../app/static/js/htmx_error_policy.js";

const { rendersInlineFormError } = HtmxErrorPolicy;

test("400 y 422 renderizan el error dentro del formulario", () => {
  // Los devuelven me.py, users.py y contacts.py como fragmento HTML que
  // swapea en el div de feedback del form.
  assert.equal(rendersInlineFormError(400), true);
  assert.equal(rendersInlineFormError(422), true);
});

test("403 y 404 NO renderizan error inline: no traen formulario util", () => {
  // 403 es el CSRF check (main.py devuelve un <p> suelto) y 404 puede
  // devolver JSON de HTTPException o el error_404.html completo. Ninguno es
  // un error de campo, así que su toast tiene que seguir saliendo.
  assert.equal(rendersInlineFormError(403), false);
  assert.equal(rendersInlineFormError(404), false);
});

test("otros 4xx quedan fuera por defecto", () => {
  assert.equal(rendersInlineFormError(401), false);
  assert.equal(rendersInlineFormError(409), false);
  assert.equal(rendersInlineFormError(429), false);
});

test("los 2xx nunca cuentan como error inline", () => {
  assert.equal(rendersInlineFormError(200), false);
  assert.equal(rendersInlineFormError(201), false);
  assert.equal(rendersInlineFormError(204), false);
});

test("los 5xx nunca cuentan como error inline: van al toast", () => {
  assert.equal(rendersInlineFormError(500), false);
  assert.equal(rendersInlineFormError(502), false);
  assert.equal(rendersInlineFormError(503), false);
});

test("entradas ausentes o basura no cuentan como error inline", () => {
  // `event.detail.xhr` puede no existir; app.js cae a 0 en ese caso.
  assert.equal(rendersInlineFormError(0), false);
  assert.equal(rendersInlineFormError(undefined), false);
  assert.equal(rendersInlineFormError(null), false);
  assert.equal(rendersInlineFormError("422"), false);
  assert.equal(rendersInlineFormError(NaN), false);
});
