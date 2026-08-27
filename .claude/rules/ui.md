---
paths:
  - "panel/app/templates/**/*.html"
  - "panel/app/static/css/**/*.css"
  - "panel/app/static/js/**/*.js"
  - "landing/**/*.html"
  - "landing/**/*.css"
  - "landing/**/*.js"
---

# Reglas de UI

Aplican a todo lo que se ve: el panel admin (Jinja2 + HTMX + Tailwind + Alpine),
el portal público `/propiedades`, y la landing. Correr `/impeccable audit` antes
de cada PR de frontend.

Adaptadas desde las reglas del proyecto Boutiques (`e-commerce/.claude/rules/ui.md`)
el 2026-08-17. Se podaron tres secciones que no transfieren — *Arquetipos*
(multi-tenant con estilos serena/cercana/cruda/editorial), *Storefront* (stock y
variantes) y *Storefront vs admin* (shadcn/Radix/TanStack) — porque Onnix no tiene
tenants ni librería de componentes: acá es Jinja2 con HTMX, y una regla que
describe otro stack es ruido que se desobedece y después se ignora entera.

## Prohibido porque parece hecho por IA

Gradientes morados · glassmorphism · serif itálica de display · cards dentro de
cards · tiles de iconos de colores · beige de IA · eyebrow chips en el hero.

**Ninguna foto de propiedad se genera ni se retoca con IA.** Onnix vende
inmuebles que existen y que la persona va a ir a visitar: una foto generada es
una casa que no va a encontrar, y en inmobiliaria eso no es un problema
estético sino de confianza y de exposición legal. Las fotos salen de
`properties.image_urls` (las que publica el portal de origen) o de la cámara de
la inmobiliaria. Si una propiedad no tiene foto, va el estado vacío — no una
imagen inventada ni una foto de banco de otra casa.

Esto no se puede lintear: ningún lint distingue un WebP generado de uno real.
Va acá y no como invariante del `CLAUDE.md` justamente por eso — un invariante
que no se verifica es un comentario disfrazado de barrera.

## Consistencia

Un solo patrón para avisos, errores, estados vacíos, confirmaciones,
prerrequisitos y wizards. Si un flujo bloquea un paso por falta de un dato
previo, **todos** lo hacen igual, con el mismo parcial de Jinja y el mismo
texto.

En un panel con HTMX esto importa más que en una SPA: cada `hx-swap` reemplaza
un pedazo de pantalla, y dos parciales que resuelven el mismo estado con
markup distinto se notan al instante cuando uno reemplaza al otro.

## Elementos

- **Iconos**: una sola familia, nunca emojis. Tamaños solo de la escala
  14 / 16 / 20 / 24.

  > El 14 entró a la escala el 2026-08-23, no porque hiciera falta un tamaño
  > más sino porque ya era el más usado: `w-3.5` aparece **38 veces en 8
  > templates**, más que cualquiera de los tres que la escala nombraba. La
  > regla describía un panel que no existe. Las dos salidas eran subir los 38 a
  > 16 o admitir el 14; se admitió el 14 porque ninguno de los 38 se veía mal,
  > y una regla que obliga a un cambio que nadie pidió se deja de leer.
  >
  > **14 es el piso.** Un icono más chico no entra a la escala: por debajo el
  > trazo de la familia deja de leerse a densidad normal.
- **Botones**: alturas solo de la escala 32 / 36 / 44. Nunca ancho arbitrario.
- **Una sola acción primaria por vista.** El resto secundarias o ghost.
- **Nunca dos botones que hagan lo mismo en una vista.** Auditar cada pantalla:
  si hay dos caminos a la misma acción, eliminar uno. Excepción: una
  confirmación efímera (toast de 3 segundos) puede repetir una acción que ya
  vive en el header — no son dos caminos estables, es el affordance del evento
  que acaba de ocurrir.
- **Estados vacíos con acción**, no disculpas.
- **Touch targets de 44×44 mínimo.** El panel se usa desde el celular en
  visitas, no solo desde el escritorio.
- **`aria-disabled` contra `disabled`**:

  > ¿La condición que apaga el control lee estado que el propio handler de ese
  > control escribe? Sí → `aria-disabled`. No → `disabled`.
  >
  > **Excepción dura: todo `type="submit"` se queda con `disabled`**, porque
  > `aria-disabled` no impide el envío del formulario.
  >
  > El estado se dice **siempre en palabras**. La señal visual va con token de
  > color, **nunca con opacidad**.

## Color

Cero hex en templates y CSS propio. Solo utilities de Tailwind mapeadas a
tokens, o `var(--token-*)`. Un hex suelto es un color que nadie va a encontrar
cuando haya que cambiar la paleta.

Contraste **WCAG AA como piso** (4.5:1 en texto normal, 3:1 en texto grande y
en bordes de controles). Si una combinación no pasa, se corrige el token, no se
tapa con un `!important`.

**Como mucho dos matices saturados decorativos en toda la interfaz.** El resto
es neutro. Un panel inmobiliario muestra datos densos —precios, estados,
contadores— y cada color de más es una señal que compite con las que importan:
el estado del lead y el precio.

**Los dos son el acento y el rojo.** El acento es la marca. El rojo se reserva para
destructivo e irreversible: `baja` es irreversible (regla 4 del `CLAUDE.md`) y
se ve como tal.

**El color de estado no gasta cupo.** Decisión de Ez del 2026-08-23. Cuando se
tomó, el panel estaba en 12 matices y 257 usos, y **141 de esos usos
(55 %) decían un estado**: 102 eran decoración y 14, marca. La regla original no
aclaraba si los contaba, y por no aclararlo el panel llegó a 12 — con el acento y
el rojo ya asignados, el éxito y la advertencia no tenían de dónde salir.

**El inventario vigente son 219 usos en 7 matices**, y ese número no se escribe
a mano: lo sostiene `panel/tests/test_matices_saturados.py`, que lo mide y no
deja que suba. Si acá dice otra cosa que ahí, el test se pone rojo.

El color de estado **no es decoración: es redundancia deliberada sobre un texto
que ya está ahí**, y por eso los 141 sitios no rompen 1.4.1 aunque se les quite
el color. El presupuesto de dos apunta a los 102 decorativos, que son los que no
dicen nada que el texto no diga.

**Qué cuenta como estado**, para que la regla no se estire: éxito, advertencia,
error y «apagado» sobre un elemento que **ya nombra ese estado en palabras**. Un
icono de color en una lista, el fondo de un contador, la tinta de una pestaña
activa y el color de una serie de gráfico **son decoración** y sí gastan cupo.

### Las dos excepciones declaradas

Decisión de Ez del 2026-08-22. **El verde de WhatsApp y el azul de Telegram se
quedan y no gastan cupo**, porque no son una elección estética: son la marca de
un tercero y lo que hacen es decir por qué canal va el mensaje. Cambiarlos no
ahorraría una señal, costaría el reconocimiento del canal.

Vale solo donde identifican el canal —el botón que abre WhatsApp, el badge que
dice que un contacto llegó por Telegram—, no como color decorativo en cualquier
otro lado.

**Esto está escrito acá porque no estarlo salió caro.** La decisión se tomó el
22/08 y quedó anotada en `ESTADO_UI.md`, que es un doc de estado, no una regla.
El 23/08 la medición encontró 12 matices saturados y 257 usos. Una excepción que
vive fuera de la regla no es una excepción: es el primer permiso de una serie.

### El techo, y quién lo sostiene

La regla la sostiene `panel/tests/test_matices_saturados.py`, que **mide, no
opina**: cuenta las clases saturadas del panel parseándolas como clases —no por
substring, porque `bg-onnix-accent-dark` contiene `bg-onnix-accent`— y filtra los
comentarios antes de contar, porque el comentario que explica una regla nombra
lo que la regla prohíbe.

El techo **solo baja**. Un matiz nuevo o un uso de más lo pone rojo nombrando el
archivo y la línea.

El mismo archivo calcula el contraste de cada combinación fondo+texto que el
panel realmente pinta, con el hex que sale de la CSS compilada. **Ningún número
está escrito a mano**: en este repo dos números a mano decían 5,79 y 2,89 y eran
11,30 y 5,65.

## Animación

150-200ms, `ease-out`, y solo estas cinco propiedades: **`transform`,
`opacity`, `color`, `background-color` y `border-color`**. Las cinco son de
composición y pintado, no de layout.

`border-color` se sumó el 2026-08-23, por decisión de Ez. La lista original
tenía cuatro y dejaba afuera una propiedad de la misma familia: cambiar el
color de un borde no altera su ancho, así que no dispara layout — cuesta lo
mismo que animar `color`. La ficha pública lo animaba en dos lugares y la
alternativa era empeorar la señal de foco de los controles para respetar una
lista que había quedado incompleta, no restrictiva a propósito.

**El que sí queda afuera es `border-width`**, y no es lo mismo: ese sí mueve
la caja.

**Nunca animar layout**, y eso es una lista, no una palabra que se interpreta:
`width`, `height`, `top`, `left`, `right`, `bottom`, `margin`, `padding` y
`font-weight`. El peso de la fuente cambia métricas de texto, o sea layout.

**`prefers-reduced-motion` se resuelve global**, en un solo bloque
`@media (prefers-reduced-motion: reduce)` dentro de `panel/app/static/css/`, no
componente por componente — esa es la versión que se olvida en el próximo
parcial. Nada rebota, nada pulsa, nada parpadea.

Con HTMX, cuidado con animar los swaps: `hx-swap` con transición larga hace que
la pantalla se sienta lenta justo donde el usuario espera respuesta inmediata.
Si el swap tarda, la señal va en el indicador de carga, no en la animación de
entrada.

## Voseo

Todo en **voseo**: el panel, la landing, el portal público, los mensajes del
bot, los docs y los comentarios. Es el castellano de Paraguay y es como ya
habla el bot (`tenés`, `querés`, `podés`, `mirá`, `escribinos` en
`panel/app/bot/ai/prompts.py`).

Sentence case siempre, nunca Title Case ni MAYÚSCULAS.

**Inconsistencia real, pendiente de decisión del owner** (relevada 2026-08-17):
los templates del panel mezclan — 4 usos de `puede` contra 5 de voseo. Antes de
unificar hay que decidir si el **portal público** `/propiedades` y la landing
le hablan de usted a quien busca casa, como hace Boutiques con su storefront, o
si todo Onnix se queda en voseo. Son destinatarios distintos: el panel lo usa
la administradora y sus asesores; el portal lo lee alguien que quizás nunca habló con
la inmobiliaria. **No unificar por las bravas hasta que esté decidido** — hoy
la regla documenta lo que hay, no lo que debería ser.
