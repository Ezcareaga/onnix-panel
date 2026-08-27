/**
 * CSRF double-submit cookie wiring for HTMX (D1).
 *
 * Reads the csrf_token cookie set by the server middleware and injects it
 * as the X-CSRFToken header on every HTMX request, fulfilling the
 * double-submit cookie pattern without requiring hidden form fields in
 * HTMX-driven forms.
 *
 * Reference: https://htmx.org/events/#htmx:configRequest
 *
 * NOTE: This file is intentionally external (not inline) so it remains
 * compatible when the CSP is later tightened to remove 'unsafe-inline'
 * from script-src. External scripts under 'self' are always allowed.
 */
(function () {
  "use strict";

  /**
   * Read a named cookie from document.cookie.
   * Returns empty string if the cookie is absent.
   *
   * @param {string} name
   * @returns {string}
   */
  function getCookie(name) {
    var match = document.cookie.match(
      new RegExp("(?:^|;\\s*)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]*)")
    );
    return match ? decodeURIComponent(match[1]) : "";
  }

  /**
   * Inject X-CSRFToken into every HTMX request header.
   * Fires on htmx:configRequest which is emitted before each HTMX XHR.
   */
  document.addEventListener("htmx:configRequest", function (evt) {
    var token = getCookie("csrf_token");
    if (token) {
      evt.detail.headers["X-CSRFToken"] = token;
    }
  });
})();
