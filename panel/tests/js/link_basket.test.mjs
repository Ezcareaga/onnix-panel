// TDD — M6.5 link basket (T6): lógica pura del acumulador de links públicos.
// Corre con `node --test "tests/js/**/*.test.mjs"` (wrapper: tests/test_js_suite.py).
import test from "node:test";
import assert from "node:assert/strict";

// link_basket.js es UMD (CJS + window) — default import via interop ESM→CJS.
import LinkBasket from "../../app/static/js/link_basket.js";

const {
  STORAGE_KEY,
  addLink,
  removeLink,
  toggle,
  clear,
  formatBlock,
  serialize,
  deserialize,
} = LinkBasket;

const CASA = {
  id: 1,
  title: "Casa en Lambaré",
  priceLabel: "USD 120,000",
  url: "https://onnix.com.py/prop/1-casa-en-lambare",
};

const DEPTO = {
  id: 2,
  title: "Depto Villa Morra",
  priceLabel: "₲ 500,000,000",
  url: "https://onnix.com.py/prop/2-depto-villa-morra",
};

test("test_copy_accumulator_adds_unique_links", () => {
  let state = clear();
  state = addLink(state, CASA);
  state = addLink(state, CASA); // mismo id 2 veces → dedup
  assert.equal(state.items.length, 1);

  state = addLink(state, DEPTO);
  assert.equal(state.items.length, 2);
});

test("test_copy_all_block_format", () => {
  let state = clear();
  state = addLink(state, CASA);
  state = addLink(state, DEPTO);
  assert.equal(
    formatBlock(state),
    "Casa en Lambaré\n" +
      "USD 120,000\n" +
      "https://onnix.com.py/prop/1-casa-en-lambare" +
      "\n\n" +
      "Depto Villa Morra\n" +
      "₲ 500,000,000\n" +
      "https://onnix.com.py/prop/2-depto-villa-morra",
  );
});

// ---------------------------------------------------------------------------
// La descripcion minima del inmueble (2026-08-23)
// ---------------------------------------------------------------------------
//
// El bloque que se pegaba en WhatsApp era titulo + precio + link. Del otro lado
// se leia un titulo escrito por el portal de origen —«Hermosa casa en venta
// zona Villa Morra»— sin un solo dato del inmueble. Ahora lleva la misma linea
// que la card muestra debajo del precio.

const CASA_CON_META = {
  ...CASA,
  meta: "Venta · Casa · 3 dorm · 2 baños · 240 m² · Villa Morra, Asunción",
};

test("test_format_block_incluye_la_descripcion_minima", () => {
  let state = addLink(clear(), CASA_CON_META);
  assert.equal(
    formatBlock(state),
    "Casa en Lambaré\n" +
      "Venta · Casa · 3 dorm · 2 baños · 240 m² · Villa Morra, Asunción\n" +
      "USD 120,000\n" +
      "https://onnix.com.py/prop/1-casa-en-lambare",
  );
});

test("test_format_block_no_deja_renglones_vacios", () => {
  // Una propiedad sin precio y sin meta: los campos vacios se saltean en vez
  // de dejar un renglon en blanco o un separador colgando.
  let state = addLink(clear(), {
    id: 9,
    title: "Terreno en Luque",
    meta: "",
    priceLabel: "",
    url: "https://onnix.com.py/prop/9-terreno",
  });
  assert.equal(
    formatBlock(state),
    "Terreno en Luque\nhttps://onnix.com.py/prop/9-terreno",
  );
});

test("test_meta_sobrevive_al_sessionStorage", () => {
  // La lista vive en sessionStorage: si `meta` no viaja en el serialize, la
  // segunda propiedad que se copia pierde su descripcion y nadie lo nota.
  const state = addLink(clear(), CASA_CON_META);
  const ida_y_vuelta = deserialize(serialize(state));
  assert.equal(ida_y_vuelta.items[0].meta, CASA_CON_META.meta);
});

test("test_un_item_viejo_sin_meta_no_rompe", () => {
  // sessionStorage puede tener items guardados antes de que `meta` existiera.
  const viejo = deserialize(
    JSON.stringify({ items: [{ id: 1, title: "Casa", priceLabel: "USD 1", url: "u" }] }),
  );
  assert.equal(viejo.items[0].meta, "");
  assert.equal(formatBlock(viejo), "Casa\nUSD 1\nu");
});

test("test_accumulator_clear", () => {
  let state = clear();
  state = addLink(state, CASA);
  state = addLink(state, DEPTO);
  state = clear();
  assert.equal(state.items.length, 0);
  assert.equal(formatBlock(state), "");
});

test("toggle_removes_existing", () => {
  let state = clear();
  state = toggle(state, CASA);
  assert.equal(state.items.length, 1);
  state = toggle(state, CASA); // re-click → lo saca
  assert.equal(state.items.length, 0);
});

test("remove_link_by_id", () => {
  let state = clear();
  state = addLink(state, CASA);
  state = addLink(state, DEPTO);
  state = removeLink(state, CASA.id);
  assert.equal(state.items.length, 1);
  assert.equal(String(state.items[0].id), "2");
});

test("deserialize_corrupt_returns_empty", () => {
  assert.deepEqual(deserialize("{not-json").items, []);
  assert.deepEqual(deserialize(null).items, []);
  assert.deepEqual(deserialize("").items, []);
  assert.deepEqual(deserialize('"una string"').items, []);
  assert.deepEqual(deserialize('{"items": "no-array"}').items, []);
});

test("serialize_roundtrip_for_session_storage", () => {
  let state = clear();
  state = addLink(state, CASA);
  const restored = deserialize(serialize(state));
  assert.equal(restored.items.length, 1);
  assert.equal(restored.items[0].title, "Casa en Lambaré");
  assert.equal(restored.items[0].priceLabel, "USD 120,000");
  assert.equal(restored.items[0].url, CASA.url);
  assert.equal(typeof STORAGE_KEY, "string");
  assert.equal(STORAGE_KEY, "onnixLinkBasket");
});
