# ONNIX SA — Bandeja unificada de canales

Panel administrativo para Onnix SA, Paraguay. El producto es **una sola bandeja
con los mensajes de WhatsApp, Instagram y Messenger**, contestados **a mano** por
una persona, más un panel para crear y administrar las plantillas.

**No hay chatbot y no vuelve a haber uno.** El cliente lo pidió explícitamente:
quiere centralizar sus canales, no automatizarlos. Si una tarea empuja a
"que conteste solo", está mal leída.

- **Desarrollador:** Ez Careaga

## De dónde salió este repo

Es un **fork del panel + bot de otra inmobiliaria**, con el branding sustituido
y sin ninguna referencia al cliente original en el código. Lo que se heredó
funciona; lo que sigue abierto está en «Lo que falta» más abajo.

Del original **no** se copiaron: los scrapers de portales, los MP4 de
tutoriales, el dump de producción, `docs/` y los scripts de un solo uso atados
a la cuenta de Twilio del cliente viejo.

**El vertical inmobiliario ya no está.** Vino entero en el fork y se borró el
2026-08-27 por decisión de Ez: Onnix no vende propiedades. Se fueron el
catálogo, el portal público, la landing, los scrapers, InfoCasas, la búsqueda
híbrida y todo lo que colgaba de ellos — routes, services, repos, modelos,
plantillas y 76 archivos de test.

Lo que quedó en la base son las tablas (`properties`, `property_types`,
`infocasas_properties`, `infocasas_inquiry_history`) y algunas columnas
(`contacts.property_id`, `contacts.infocasas_ref`, `messages.properties_shown`,
`visits.property_id`). **Están vacías y nadie las lee.** No se borraron porque
media docena de migraciones las crean y modifican, y sacarlas del baseline
rompe la cadena — el mismo problema que ya costó arreglar en 004, 005, 006,
019, 033 y 037.

**ponytail:** cuando el producto se estabilice, la salida es aplastar la cadena
entera en un baseline nuevo sin nada de esto, no ir migración por migración.

## El bot que ya no está

El panel venía de un proyecto con un bot conversacional que contestaba solo por
WhatsApp con tool-use de Claude. Se sacó entero el 2026-08-27: `app/bot/ai/`
(prompts, tools, tool_use_loop, circuit_breaker), `app/bot/core/`
(orchestrator, tool_executor, response_builder, name_gate), `app/bot/handlers/`
completo, la tarea `followup_sender` y 46 archivos de test.

**El corte fue de una línea por webhook.** El entrante ya se persistía ANTES de
todo lo que podía fallar (`persist_inbound`, en `panel/app/bot/core/conversation.py`),
así que sacar `deps.wa_handler.handle(...)` deja la bandeja intacta: el mensaje
entra, el SSE lo empuja al panel, y contesta una persona.

Lo que quedó de `app/bot/` NO le habla a nadie:

| Módulo | Para qué sigue |
|---|---|
| `channels/` | transporte de salida — lo usa el envío manual del panel |
| `webhooks/` | entrada de mensajes |
| `panel/app/bot/core/conversation.py` | `persist_inbound` + resolver contacto/conversación |

`app/bot/search/` y los dos clientes de LLM se fueron después, con el vertical
inmobiliario: existían para buscar en el catálogo.

`BOT_ENABLED` quedó sin lectores y se sacó de los compose: no apagaba nada.

Definidas en los módulos de tool-use:

| Tool | Qué hace |
|---|---|
| `search_properties` | Busca en el catálogo. 15 parámetros: operación, tipo, ciudad, barrio(s), rango de precio y moneda, dormitorios y baños (min/max), superficie (min/max), estado de construcción y `descripcion_libre` para lo semántico. |
| `get_property_detail` | Ficha completa. Acepta ordinal («la primera», «la 3») o ID. |
| `register_lead` | Deriva a un asesor humano. **No exige nombre**: si el cliente lo evade, deriva con captura parcial. |
| `process_opt_out` | Baja del bot. **Irreversible.** |
| `resolver_zona` | Texto ambiguo de zona («por san ber», «cerca del centro») a ciudad/barrio/landmark canónico. |
| `agendar_visita` | Agenda visita cuando el cliente ya confirmó día y hora. Nunca ve `contact_id` — lo inyecta el handler desde el `search_context`. |

`get_tools(mode)` entrega dos repertorios:

- **`busqueda`** (default): las 6.
- **`recepcionista`**: las 6 menos `search_properties` — ese bot califica leads,
  no busca.

## Lo que falta

1. **Los tres canales, directo a Meta.** Hoy hay WhatsApp por **Twilio** y
   Telegram. Van los tres por la Graph API de Meta, que comparten el mismo
   handshake (`hub.mode` / `hub.verify_token` / `hub.challenge`), la misma
   firma `X-Hub-Signature-256` con el app secret, y el mismo token de System
   User. Lo único que cambia es el `object` del payload:
   `whatsapp_business_account`, `page` (Messenger) o `instagram`.
   Por eso va **un solo** `/webhooks/meta` que despacha por `object`, y no tres.
   La abstracción ya existe: `panel/app/bot/channels/base.py` define
   `BaseSender`, `panel/app/bot/webhooks/router.py` compone los sub-routers.
   Falta además una migración que extienda el CHECK de `conversations.channel`
   (hoy `whatsapp | web | manual | telegram`, fijado en
   `panel/alembic/versions/003_add_telegram_channel.py`).
2. **Panel de plantillas.** Hoy las plantillas son una lista de claves
   **hardcodeada en Python** (`panel/app/schemas/template.py`), cada una
   apuntando a un ContentSid de Twilio guardado en `bot_settings`. Crear una
   plantilla nueva es editar código y desplegar. El cliente quiere crearlas
   desde el panel: hace falta tabla propia, CRUD, alta contra la Message
   Templates API de Meta y el estado de aprobación, que llega por el webhook
   `message_template_status_update`.
3. **Infraestructura.** No hay VPS, dominio, base ni pipeline definidos todavía.
   Los `docker-compose.yml` / `docker-compose.dev.yml` y los `nginx_*.conf`
   vienen del original con los nombres cambiados: **hay que revisarlos antes de
   levantar nada**, no están verificados contra un servidor real. Para probar
   webhooks en la laptop va un túnel (`cloudflared tunnel --url
   http://localhost:8010`); Meta exige HTTPS público.
4. **Limpieza de UI del bot**: la pantalla «Salud del Bot», los toggles de bot
   en Configuración y las métricas de IA siguen ahí y ya no miden nada.
5. **Residuo cosmético del bot**: el estado `bot_replied` de `contacts` sigue
   apareciendo como «Bot respondió» en el funnel y en los filtros de leads, y
   un hilo puede decir «Bot en pausa». Son valores de la columna `status`, así
   que renombrarlos es una migración con datos, no un cambio de plantilla.

## Marca

Blanco y negro, con más blanco que negro. Los tokens son la única fuente de
color y viven en el `:root` de `panel/app/static/css/custom.css`, replicados en
`panel/tailwind.config.js` — `panel/tests/test_color_tokens.py` verifica que no
diverjan.

- `--accent: #16181A` es **el negro de marca**: relleno de la única acción
  primaria de la vista, siempre con texto **blanco** encima (17,80:1).
- `--accent-wash: #ECECEA` es la superficie de selección — gris neutro, porque
  un acento negro no tiene tinte claro propio.
- El shell (barra lateral y login) sigue oscuro a propósito: es el negro del
  par. Todo el área de trabajo es blanca.
- El portal público y `landing/` quedaron en tema oscuro con acento **blanco**.
  Invertirlos a claro es un carril aparte y no está hecho.

La regla de contraste se invirtió respecto del panel original y la sostiene
`panel/tests/test_accent_contrast.py`: donde antes el texto sobre el acento
tenía que ser oscuro, ahora tiene que ser blanco.

El logo es `panel/app/static/img/onnix_logo.svg` — un SVG con la Outfit
incrustada en base64, porque un SVG dentro de un `<img>` no hereda el
`@font-face` de la página.

## Reglas inquebrantables

Violarlas es un bug de seguridad o de datos, no una preferencia de estilo.

1. NUNCA hardcodear credenciales — todo en `.env`.
2. NUNCA `DROP TABLE` con datos — siempre migraciones.
3. NUNCA borrar propiedades — `is_active = FALSE`.
4. `baja` / opt-out es **IRREVERSIBLE**.
5. El usuario de WhatsApp **NUNCA** ve un error técnico.
6. SIEMPRE verificar docs oficiales con **Context7** antes de implementar.
7. SIEMPRE `unaccent()` en búsquedas en español.
8. SIEMPRE Alembic, NUNCA `ALTER TABLE` a mano.
9. NUNCA commitear credenciales.
10. NUNCA un servicio en staging que toque APIs externas sin su guard.

**Arquitectura por capas, sin excepción:** un `route` nunca hace SQL directo; un
`repository` nunca tiene lógica de negocio. El bot y el panel comparten los
services: cuando la lógica se filtra a una ruta, aparecen dos verdades.

## Aislamiento staging/producción (OBLIGATORIO)

Staging hereda el `.env` de producción. Sin overrides usa credenciales reales y
**manda mensajes a gente real**. `docker-compose.dev.yml` SIEMPRE debe tener:

```yaml
- INFOCASAS_POLL_ENABLED=false
- WA_SEND_ENABLED=false
- TELEGRAM_NOTIFICATIONS_ENABLED=false
```

- NUNCA agregar a staging un servicio que llame APIs externas sin su guard
  **primero**.
- Toda variable nueva que toque un servicio externo entra con su `=false` en
  `docker-compose.dev.yml`, **en el mismo commit**.
- El guard se consulta **donde se abre el socket**, no donde parece razonable.
  En el panel original siete caminos llegaban a Twilio y seis no tenían guard.

## Git

- NUNCA commit directo en `main`. Branch desde `dev`.
- Merges **siempre `--no-ff`**. Commits atómicos: `feat:` `fix:` `test:`
  `refactor:` `docs:` `chore:` `ops:`. El mensaje cuenta la decisión, no el diff.
- **NO merge sin confirmación de Ez.**

## Testing

- `pytest` para TODA lógica nueva. Test primero.
- **VERDE = pytest corrido de verdad, con la salida a la vista.**
- La base de test se arma con `scripts/make_test_db.sh` (`pg_dump --schema-only`
  + `scripts/seed_test.sql`). La cadena de Alembic **no** crea la base desde
  cero: `scripts/schema.sql` ya trae el estado post-004 adentro.

### Un test verde no es un test que prueba algo

**El criterio de verde se elige al planificar, no al ejecutar.**

| La tarea es | El criterio de verde es |
|---|---|
| lógica (repos, servicios, rutas, parsers) | **mutación de sanidad** |
| visual (templates, CSS, tokens, layout) | **verificación en el navegador** |
| infraestructura (units, nginx, permisos) | **comando de verificación + prueba negativa** |

**Mutación de sanidad:** después de llegar a verde, romper a propósito la lógica
que el test dice cubrir y confirmar que se pone **rojo**; revertir. Si sigue
verde, el test es decorativo.

**Un test que verifica contraste calcula el número, no lo copia de un
comentario.**

### Seis formas de mentir que ya pasaron en el panel original

1. **Verde que no prueba nada.** La mutación lo mata.
2. **Rojo que no habla del código.** Si un test falla con algo que **no es
   `AssertionError`**, sospechar del entorno.
3. **Skip silencioso.** Un skip tiene que nombrar qué falta.
4. **Test acoplado a lo accidental.** No identificar elementos por clases de
   estilo.
5. **Assert por substring.** `bg-onnix-accent-dark` contiene `bg-onnix-accent`.
   Assertar token exacto.
6. **Parametrizar sobre lo que se quiere probar.** Un test sobre una lista no
   puede ver una eliminación de esa lista.

Y la trampa propia de este repo: **si el test prohíbe un patrón, el comentario
que lo explica lo contiene.** Filtrar comentarios —y docstrings— antes de
assertar.

## Base de datos

`contacts`: `new → bot_replied → agent_replied → interested → closed`, más
`no_response | discarded | deleted | visit_scheduled`. `contacted` y
`negotiation` están **prohibidos por CHECK constraint**. Alembic va hasta `046`.

## Trampas conocidas

- **`TWILIO_WHATSAPP_NUMBER` va CON el prefijo `whatsapp:`.** Un `+595…` pelado
  pisa el default correcto y Twilio rechaza **todo** mensaje saliente, en
  silencio.
- **`TEST_ADMIN_PASSWORD` no está en `.env.example`** y `panel/tests/conftest.py`
  la necesita. Sin ella ~400 tests fallan con `303` y parece que la app está rota.
- **Para restaurar un `.dump` usar `pg_restore`, nunca `pg_dump | psql`**: desde
  PG 16.10 el dump trae un `\restrict` que deja a psql en modo restringido.
- **`tailwind.css` es un artefacto generado y se commitea a mano.** Recompilar
  con `npx tailwindcss@3.4.19 -i app/static/css/input.css -o
  app/static/css/tailwind.css --minify` y subir el `?v=` de
  `panel/app/templates/base.html`. Lo cubre `panel/tests/test_tailwind_build_is_current.py`.

## Skills y subagentes

| Tarea | Skill |
|---|---|
| Bug / error | `systematic-debugging` |
| Feature nueva | `brainstorming` + `writing-plans` + `test-driven-development` |
| Refactor | `hexagonal-architecture` + `writing-plans` |
| UI | `.claude/rules/ui.md` manda; una skill por tarea y nunca dos |
| Antes de decir "listo" | `verification-before-completion` |
| Claude API / tool-use | `claude-api` |
| WhatsApp / Twilio | `twilio-whatsapp` |

Subagentes en `.claude/agents/`: **`architect`** planifica y escribe en
`.planning/` (un hook le bloquea escribir código); **`editor`** ejecuta un plan
aprobado, tarea por tarea. El que planifica no edita, el que ejecuta no redefine.

## Código en prompts del usuario

Todo bloque de código en un prompt es **pseudocódigo**. NUNCA copiar y pegar.
Buscar los nombres reales en el proyecto. Si algo mencionado no existe →
preguntar antes de inventar.
