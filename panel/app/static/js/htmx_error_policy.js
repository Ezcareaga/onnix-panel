/**
 * htmx_error_policy.js — política de estados HTTP de los handlers htmx.
 *
 * Lógica PURA, sin DOM. Vive aparte de app.js porque app.js registra seis
 * listeners a nivel top y no es importable desde node sin guardas; esto sí lo
 * es, y así la política queda cubierta por `node --test`.
 *
 * ¿Qué decide? Cuáles respuestas de error ya le explicaron el problema al
 * usuario DENTRO del formulario, y por lo tanto no necesitan además un toast.
 *
 * Contexto del bug que originó el archivo: `htmx:beforeSwap` ponía
 * `event.detail.isError = false` para todo 4xx. htmx deriva el resultado de
 * ahí (`i.failed=f; i.successful=!f` en htmx-2.0.4.min.js), así que un 422
 * llegaba a `htmx:afterRequest` marcado como éxito y los templates que
 * preguntan por `$event.detail.successful` cerraban el modal, reseteaban el
 * form y mostraban un toast verde sin que el servidor hubiera guardado nada.
 *
 * Dos hechos del minificado que sostienen la solución:
 *   - `if(m.shouldSwap)` se evalúa aparte de `isError` → el cuerpo inline del
 *     4xx swapea igual dejando isError en true.
 *   - `if(f){ fe(o,"htmx:responseError", ...) }` → el toast de error sale solo
 *     si isError quedó en true.
 * Entonces: isError se deja en true (para que `successful` no mienta) y el
 * toast se silencia solo acá.
 *
 * Mecanismo compartido browser + node (UMD mínimo, sin package.json extra —
 * package.json está gitignoreado en este repo):
 *   - browser: <script defer> clásico en base.html → window.HtmxErrorPolicy.
 *   - node --test: CJS via module.exports; el test .mjs lo importa con
 *     interop ESM→CJS.
 */
(function (global) {
  "use strict";

  /**
   * Estados cuyo cuerpo es un fragmento de error de formulario, listo para
   * swapear en el div de feedback del form.
   *
   * 400 y 422 son los que devuelven `me.py`, `users.py` y `contacts.py` ante
   * validación fallida. Quedan afuera a propósito:
   *   - 403: CSRF check, un <p> suelto de `main.py`. No es error de campo.
   *   - 404: puede ser JSON de HTTPException o el error_404.html entero.
   * Los dos siguen necesitando su toast, porque su cuerpo no le dice al
   * usuario qué corregir.
   */
  var INLINE_FORM_ERROR_STATUSES = [400, 422];

  /**
   * ¿Este estado ya mostró el error dentro del formulario?
   *
   * @param {number} status - status del xhr. Tolera 0 / undefined / null,
   *   que es lo que app.js pasa cuando `event.detail.xhr` no existe.
   * @returns {boolean}
   */
  function rendersInlineFormError(status) {
    if (typeof status !== "number") return false;
    if (!isFinite(status)) return false;
    return INLINE_FORM_ERROR_STATUSES.indexOf(status) !== -1;
  }

  var HtmxErrorPolicy = {
    INLINE_FORM_ERROR_STATUSES: INLINE_FORM_ERROR_STATUSES,
    rendersInlineFormError: rendersInlineFormError,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = HtmxErrorPolicy; // node --test (CJS)
  }
  if (global) {
    global.HtmxErrorPolicy = HtmxErrorPolicy; // browser
  }
})(typeof window !== "undefined" ? window : undefined);
