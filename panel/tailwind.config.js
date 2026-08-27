/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/constants.py",
    "./app/routes/**/*.py",
  ],
  theme: {
    extend: {
      // Estos nombres tienen que coincidir con el :root de
      // app/static/css/custom.css. Eran una segunda paleta paralela:
      // onnix-black decia #1A1A1A donde el :root dice #16181A, asi que el
      // mismo "negro de marca" salia distinto segun quien lo pintara.
      // Lo cubre tests/test_color_tokens.py.
      colors: {
        'onnix-accent': '#16181A',        // --accent
        'onnix-black': '#16181A',       // --shell / --ink-900
        'onnix-accent-ink': '#16181A',    // --accent-ink: el acento como TEXTO sobre claro
        // --accent-wash. Existia en el :root de custom.css sin utility, y por eso
        // 13 superficies de seleccion del panel se pintaban `amber-50`: oro con
        // otro nombre, invisible a cualquier cambio de paleta. Decision de Ez del
        // 2026-08-23. El hex se repite aca porque custom.css se sirve sin
        // compilar y Tailwind necesita el valor, no la variable — igual que
        // onnix-accent. Lo cubre tests/test_color_tokens.py.
        'onnix-accent-wash': '#ECECEA',   // --accent-wash: superficie de seleccion
        // La escala de tinta del :root, para que los templates dejen de pedirle
        // grises a Tailwind. text-gray-400 daba 2,54:1 y text-gray-300, 1,47:1.
        'onnix-ink-900': '#16181A',     // --ink-900, 17,80:1 sobre --surface
        'onnix-ink-600': '#55595E',     // --ink-600,  7,05:1
        'onnix-ink-400': '#6B7075',     // --ink-400,  5,00:1
        'onnix-rule': '#DFDFDC',        // --rule: separadores, no texto
        'onnix-rule-strong': '#8C8C88', // --rule-strong, 3,15:1: bordes de control,
                                      // iconos decorativos y estados deshabilitados
        // Sin token todavia, cada uno espera a su carril:
        'onnix-accent-light': '#3A3D40',  // hover del primario — carril B3
        'onnix-accent-dark': '#000000',   // hover del primario — carril B3
        'onnix-dark': '#2D2D2D',        // fondo de la card de login — carril K
        'onnix-gray': '#3A3A3A',        // borde sobre oscuro — carril K
      },
      // Una sola familia, self-hosteada y variable: las @font-face estan en
      // custom.css. `display` sigue existiendo como nombre porque 40 templates
      // lo usan para los numeros grandes, pero apunta a la misma Outfit — lo
      // que los distingue es el peso, no la familia. `luxe` (Cormorant) se
      // fue: su unico uso era el precio de la ficha, que pasa a Outfit 600.
      fontFamily: {
        'sans': ['Outfit', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        'display': ['Outfit', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
      },
    }
  },
  plugins: [],
}
