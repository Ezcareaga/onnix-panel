/**
 * link_basket.js — M6.5 T6: acumulador de links públicos (lógica PURA, sin DOM).
 *
 * El asesor copia links públicos de propiedades para mandarlos por WhatsApp,
 * de a uno o acumulando varios. El clipboard no es apendable → la acumulación
 * vive en este estado (persistido en sessionStorage por el Alpine store de
 * base.html).
 *
 * Mecanismo compartido browser + node (UMD mínimo, sin package.json extra —
 * package.json está gitignoreado en este repo):
 *   - browser: <script defer> clásico en base.html → window.LinkBasket.
 *   - node --test: CJS via module.exports; el test .mjs lo importa con
 *     default import (interop ESM→CJS de node).
 *
 * Shape del estado: { items: [{ id, title, meta, priceLabel, url }] } — id siempre String.
 *
 * `meta` es la descripcion minima del inmueble (operacion · tipo · dormitorios ·
 * m² · barrio, ciudad). Entro el 2026-08-23: el bloque que se pegaba en WhatsApp
 * era titulo + precio + link, y del otro lado se leia un titulo que lo escribio
 * el portal de origen —«Hermosa casa en venta zona Villa Morra»— sin un solo
 * dato. La compone el macro `resumen()` de properties/partials/_resumen.html,
 * que es el MISMO que dibuja la linea de la card: lo que el asesor ve es lo que
 * el cliente lee.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "onnixLinkBasket";

  function emptyState() {
    return { items: [] };
  }

  function has(state, id) {
    if (id === null || id === undefined) return false;
    const sid = String(id);
    return state.items.some((i) => String(i.id) === sid);
  }

  /** Agrega un link al final (dedup por id). Devuelve un estado NUEVO. */
  function addLink(state, item) {
    if (!item || item.id === null || item.id === undefined) return state;
    if (has(state, item.id)) return state;
    return {
      items: [
        ...state.items,
        {
          id: String(item.id),
          title: String(item.title ?? ""),
          meta: String(item.meta ?? ""),
          priceLabel: String(item.priceLabel ?? ""),
          url: String(item.url ?? ""),
        },
      ],
    };
  }

  /** Saca un link por id. Devuelve un estado NUEVO. */
  function removeLink(state, id) {
    const sid = String(id);
    return { items: state.items.filter((i) => String(i.id) !== sid) };
  }

  /** Re-click sobre la misma prop la saca de la lista; si no estaba, la agrega. */
  function toggle(state, item) {
    if (item && has(state, item.id)) return removeLink(state, item.id);
    return addLink(state, item);
  }

  function clear() {
    return emptyState();
  }

  /**
   * Bloque de texto plano listo para pegar en WhatsApp, una propiedad por
   * parrafo:
   *
   *     Casa en Venta en Villa Morra
   *     Venta · Casa · 3 dorm · 2 baños · 240 m² · Villa Morra, Asuncion
   *     USD 185.000
   *     https://onnix.com.py/prop/42-casa-en-venta-villa-morra
   *
   * Cada campo en su renglon y ninguno vacio: WhatsApp no corta lineas cortas,
   * y un " — " colgando cuando falta el precio se lee como un error. Antes era
   * "{title} — {priceLabel}\n{url}" en una sola linea, sin un solo dato del
   * inmueble: el titulo lo escribio el portal de origen y suele no informar
   * nada.
   */
  function formatBlock(state) {
    return state.items
      .map((i) =>
        [i.title, i.meta, i.priceLabel, i.url]
          .map((campo) => String(campo ?? "").trim())
          .filter((campo) => campo !== "")
          .join("\n")
      )
      .join("\n\n");
  }

  function serialize(state) {
    return JSON.stringify({ items: state.items });
  }

  /** Tolerante a JSON corrupto / shape inesperado → estado vacío. */
  function deserialize(raw) {
    if (typeof raw !== "string" || raw === "") return emptyState();
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      return emptyState();
    }
    if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.items)) {
      return emptyState();
    }
    return {
      items: parsed.items
        .filter((i) => i && typeof i === "object" && i.id !== null && i.id !== undefined)
        .map((i) => ({
          id: String(i.id),
          title: String(i.title ?? ""),
          meta: String(i.meta ?? ""),
          priceLabel: String(i.priceLabel ?? ""),
          url: String(i.url ?? ""),
        })),
    };
  }

  const LinkBasket = {
    STORAGE_KEY,
    emptyState,
    has,
    addLink,
    removeLink,
    toggle,
    clear,
    formatBlock,
    serialize,
    deserialize,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = LinkBasket; // node --test (CJS)
  }
  if (global) {
    global.LinkBasket = LinkBasket; // browser
  }
})(typeof window !== "undefined" ? window : undefined);
