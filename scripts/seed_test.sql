-- ============================================================================
-- Datos mínimos compartidos que la suite de pytest asume — TD-OPS-04, paso 1
-- ============================================================================
-- Lo carga scripts/make_test_db.sh sobre una base recién creada, después del
-- schema. Se invoca con  -v admin_password=…  tomado de $TEST_ADMIN_PASSWORD:
-- acá NO se escribe ninguna contraseña ni ningún hash (regla 1 del CLAUDE.md).
--
-- Qué va acá: SÓLO lo que varios tests dan por existente antes de correr. Lo
-- que un test crea para sí mismo no va acá.
--
-- Por qué hace falta: la base del worker se construye con el schema al día
-- pero con `alembic stamp head`, no `upgrade`. Dieciséis migraciones INSERTAN
-- filas de configuración (bot_settings, property_types) y `stamp` no las
-- corre. Esas filas son la mayor parte de este archivo.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 1. Usuarios del panel
-- ---------------------------------------------------------------------------
-- conftest.py hace UN login real con ez@onnix.com.py y replica esa
-- cookie en cada admin_client: sin esta fila la suite entera se cae con 303.
-- Los otros tres son los ids que los tests de roles y de auditoría dan por
-- existentes. Los hashes los calcula pgcrypto acá — ninguno viaja al repo.
INSERT INTO users (id, email, password_hash, name, role, is_active) VALUES
    (1, 'admin@onnix.com.py', crypt(:'admin_password', gen_salt('bf', 12)), 'Administrador', 'admin', true),
    (3, 'ez@onnix.com.py',    crypt(:'admin_password', gen_salt('bf', 12)), 'Ez',            'admin', true),
    (4, 'operaciones@onnix.com.py', crypt(:'admin_password', gen_salt('bf', 12)), 'Operaciones', 'user',  true),
    (6, 'alexis@onnix.com.py',    crypt(:'admin_password', gen_salt('bf', 12)), 'Alexis',     'user',  true)
ON CONFLICT (email) DO UPDATE
   SET password_hash = EXCLUDED.password_hash,
       role = EXCLUDED.role,
       is_active = EXCLUDED.is_active;

-- La secuencia queda por encima de los ids explícitos de arriba: si no, el
-- primer usuario que cree un test choca con la PK.
DO $$ BEGIN
    PERFORM setval(pg_get_serial_sequence('users', 'id'),
                   GREATEST((SELECT max(id) FROM users), 1));
END $$;


-- ---------------------------------------------------------------------------
-- 2. property_types — tabla de referencia, la siembra la migración 034
-- ---------------------------------------------------------------------------
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (1, 'CASA', 'Casa', 'Casa residencial, chalet, townhouse. Incluye casa en condominio/barrio cerrado. NO incluye duplex (2+ plantas con acceso independiente) ni quinta (terreno >1000m2 con amenidades rurales).', 1) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (2, 'DEPARTAMENTO', 'Departamento', 'Departamento, monoambiente, estudio, loft, penthouse. Incluye "en pozo" y variantes (con jardin, con servicio de hotel). NO incluye duplex de 2 plantas ni edificio completo.', 2) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (3, 'DUPLEX', 'Duplex', 'Vivienda de 2 o mas niveles con acceso independiente. Incluye triplex. Puede estar dentro de un edificio o ser unidad aislada.', 3) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (4, 'TERRENO', 'Terreno', 'Lote/terreno urbano o suburbano sin construccion principal, o con construccion menor. Superficie tipica <5 hectareas. NO incluye campos rurales ni quintas con amenidades.', 4) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (5, 'OFICINA', 'Oficina', 'Oficina comercial o corporativa. Incluye "oficinas" (plural) y pisos de oficinas.', 5) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (6, 'LOCAL', 'Local', 'Local comercial, tienda, salon de ventas. NO incluye depositos ni naves industriales.', 6) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (7, 'DEPOSITO', 'Deposito', 'Deposito, nave industrial, galpon, bodega, fabrica. Espacio de almacenamiento o produccion.', 7) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (8, 'QUINTA', 'Quinta', 'Propiedad recreativa con terreno amplio (tipicamente >1000m2) y amenidades: piscina, quincho, jardin extenso, ambiente rural/country. Puede ser "casa quinta".', 8) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (9, 'CAMPO', 'Campo', 'Propiedad rural/agricola/ganadera. Superficie tipicamente >5 hectareas. Incluye estancia, hacienda, propiedad agricola, livestock farm.', 9) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (10, 'EDIFICIO', 'Edificio', 'Edificio completo en venta (no unidades individuales). Uso residencial, comercial o mixto.', 10) ON CONFLICT DO NOTHING;
INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES (99, 'OTRO', 'Otro', 'Estacionamiento, fraccionamiento, inmueble productivo, uso especial. No clasificable en las categorias anteriores.', 99) ON CONFLICT DO NOTHING;

DO $$ BEGIN
    PERFORM setval(pg_get_serial_sequence('property_types', 'id'),
                   GREATEST((SELECT max(id) FROM property_types), 1));
END $$;


-- ---------------------------------------------------------------------------
-- 3. bot_settings — configuración del bot, la siembran 16 migraciones
-- ---------------------------------------------------------------------------
-- Los dos valores de sesión de InfoCasas (phpsessid, frontend_token) van con
-- un PLACEHOLDER a propósito: son credenciales vivas y no entran al repo. Las
-- filas SÍ tienen que existir — hay tests de SEC-01 que verifican que el
-- service las filtra, y una fila ausente no prueba nada.
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('vip_price_threshold_usd', '200000', 'Precio USD a partir del cual el lead va directo a la administradora', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_wa_delay_min', '1', 'Delay minimo en minutos antes de enviar WhatsApp al lead de InfoCasas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_wa_delay_max', '5', 'Delay maximo en minutos antes de enviar WhatsApp al lead de InfoCasas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_reply_delay_min', '60', 'Delay minimo en minutos antes de responder en InfoCasas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_reply_delay_max', '300', 'Delay maximo en minutos antes de responder en InfoCasas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('working_hours_start', '08:00', 'Hora inicio horario laboral (PYT)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('working_hours_end', '20:00', 'Hora fin horario laboral (PYT)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('bot_off_message', 'Hola! En este momento nuestro asistente virtual no está disponible. Podés escribirnos directo haciendo click acá 👉 https://wa.me/595986255242 y te respondemos a la brevedad!', 'Mensaje cuando bot esta apagado', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('whatsapp_mode', 'auto', 'WhatsApp bot mode: manual (human only) or auto (bot responds)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_detalle', '', 'ContentSid: detalle con Asesor/Mas/Buscar', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_ic_welcome_v3', 'HX6277b8bb2b1afaae35252f8e6fbec8b6', 'IC lead directo — cliente consulta propiedad en InfoCasas, hay match (v3 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_ic_reenviado_welcome_v3', 'HXcde10061970ff6c50d440d8878d4f743', 'IC lead reenviado — cliente consulta en IC, sin match exacto (v3 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_send_property_v4', 'HX247203d3c375f283eecebfc9aa393936', 'Asesor envía propiedad específica desde el panel al contacto (v4 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_send_preferences_v4', 'HX9b6bb69795e230c9eadf93483b225846', 'Asesor envía propiedades según preferencias del contacto desde el panel (v4 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_send_generic_v3', 'HX5b5287b102b892aec74b3b98142e8f12', 'Contacto nuevo sin contexto de propiedad — apertura de conversación (v3 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_res2', 'HXa08b78c24ede577f251d3e6dbd7f370a', 'ContentSid: resultados 2 props con Detalle/Mas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_followup_v3', 'HXd5432455c9dd899fa2f3e794f60fbd74', 'Follow-up automático a las 24h sin respuesta del contacto (v3 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_followup_72h_v3', 'HX3156462f1bf85c558a71b0f88bffa3d1', 'Follow-up automático a las 72h sin respuesta del contacto (v3 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_agent_reply_v3', 'HX62bbae4ee2d1e6ed0cd3d4e831c4d639', 'Reactivación de conversación por asesor tras silencio del bot (v3 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_ic_recurrente_directo_v2', 'HX10498d3d1a65f53fb52f8ff6ca0b9004', 'Cliente recurrente — nueva consulta IC con match directo (v2 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_ic_recurrente_reenviado_v2', 'HXc04a6df3a828550d7cec9e29df0e36ca', 'Cliente recurrente — nueva consulta IC reenviada sin match exacto (v2 Onnix)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('human_cooldown_minutes', '30', 'Minutes bot stays silent after human agent replies', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('scheduler_verification_scraper_enabled', 'true', 'Enables midday verification scraper that re-checks portal URLs for active properties', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('max_template_per_day', '1', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('followup_max_attempts', '3', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('followup_cooldown_hours', '48', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_ic_welcome', 'HXd730f59cf59fc777df36cb611c2df77a', 'ContentSid: infocasas lead welcome (needs approval)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_paginacion', '', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_res2_con_pendientes', 'HXb2d13fd61eb51674efc6a9980dc8b09f', 'Template 2 resultados + botón Más opciones', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_busqueda', '', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_res1_con_asesor', '', 'Template 1 resultado + botón Hablar c/ asesor', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('m5_zero_results_alternatives_enabled', 'true', 'M5: activa AlternativesBuilder cuando búsqueda retorna 0 resultados', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('m5_construction_state_filter_enabled', 'true', 'M5: usa columna construction_state en sql_filters en lugar de ILIKE', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('properties_chatbot_enabled', 'true', 'Enables the natural-language search chatbot in the properties panel', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_send_property', 'HXedfe80e3892c2f11cccf1237c1e0b157', 'Template: envio propiedad especifica', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_send_preferences', 'HXc801f8686a310da00881447a5255c3b7', 'Template: envio por preferencias de zona', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_send_generic', 'HX4e6c25224500055893393bbe97e0658f', 'Template: contacto nuevo generico', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('ic_autoreply_enabled', 'false', 'Enviar mensaje de bienvenida WA a leads nuevos de InfoCasas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('cleanup_inactive_refs', 'true', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('team_mention_singular', 'un asesor', 'Texto genérico usado por el bot cuando menciona un asesor del equipo en singular. Sin nombres propios.', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('scheduler_followup_sender_enabled', 'true', NULL, now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('ic_autoreply_reenviados_enabled', 'true', 'Habilitar autoreply de IC para leads reenviados (v17)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_poll_interval_min', '5', 'Minutos entre cada polling de InfoCasas', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('team_mention_collective', 'el equipo comercial', 'Texto genérico usado por el bot cuando menciona al equipo en conjunto. Sin nombres propios.', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_phpsessid', 'PLACEHOLDER-no-es-una-sesion', 'PHP session ID from InfoCasas (cookie: PHPSESSID)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('wa_tpl_opt_out', '', 'Opt-out es texto plano, sin template', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('bot_default_mode', 'busqueda', 'Modo global del bot: recepcionista | busqueda', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('infocasas_frontend_token', 'PLACEHOLDER-no-es-un-token', 'JWT token from InfoCasas login (cookie: frontend_token)', now(), NULL) ON CONFLICT DO NOTHING;
INSERT INTO bot_settings (key, value, description, updated_at, updated_by) VALUES ('bot_enabled', 'true', 'Switch global del bot. false = bot apagado, mensajes de ausencia', now(), NULL) ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- 4. Propiedades que los tests nombran por id
-- ---------------------------------------------------------------------------
-- test_property_search.py tiene tres ids escritos a mano (544672, 13708,
-- 518024) con su external_id y su título: son datos públicos de aviso, sin
-- ningún dato personal. Se siembran con esos valores exactos para que los
-- tests sigan probando lo que dicen probar en vez de reescribirlos.
-- Las otras dos existen para que el gap de stock tenga stock que contar.
INSERT INTO properties
    (id, source, external_id, title, city, property_type, property_type_normalized,
     operation, price_usd, is_active, on_hold)
VALUES
    (544672, 'remax',       '143026190-9',  'ALQUILO DEPARTAMENTO AMOBLADO',
     'Luque', 'departamento', 2, 'alquiler',   660.84, true,  false),
    (13708,  'remax',       '143011036-108','PROPIEDAD DE 10 HECTAREAS EN CERRITO',
     'Capitán Miranda', 'terreno', 4, 'venta', 165210.25, true, false),
    -- Inactiva a propósito: los dos ValueError de create/update la necesitan.
    (518024, 'onnixpy', '34747',        'Departamento tres dormitorios remodelado Asuncion centro piso 8',
     'Catedral', 'departamento', NULL, 'venta', 70965.61, false, false),
    -- El gap de demanda vs stock necesita stock en asuncion|casa y en un
    -- property_type que matchee 'duplex' por prefijo.
    (900001, 'manual', 'SEED-CASA-ASU',   'Casa seed Asunción',
     'Asunción', 'casa', 1, 'venta', 120000, true, false),
    (900002, 'manual', 'SEED-DUPLEX-LUQ', 'Duplex seed Luque',
     'Luque', 'casa-duplex', 1, 'venta', 95000, true, false)
ON CONFLICT (source, external_id) DO NOTHING;

DO $$ BEGIN
    PERFORM setval(pg_get_serial_sequence('properties', 'id'),
                   GREATEST((SELECT max(id) FROM properties), 1));
END $$;


-- ---------------------------------------------------------------------------
-- 5. Contactos de referencia
-- ---------------------------------------------------------------------------
-- Teléfonos fuera de los prefijos +595981[5-9]: si cayeran adentro, el
-- cleanup de conftest.py los borraría entre archivo y archivo y el segundo
-- test que los necesite fallaría sin decir por qué.
--   * el de whatsapp da la fila de demanda del gap y el "al menos 1 lead"
--     que pide lead_repo;
--   * el de import:excel es el único origen que contact_repo exige ver.
-- contacts no tiene UNIQUE(phone) — el idempotente va por NOT EXISTS.
INSERT INTO contacts (name, phone, source, status, property_id, created_at, last_activity_at)
SELECT v.name, v.phone, v.source, 'new', v.property_id, v.created_at, v.created_at
  FROM (VALUES
        ('Seed Demanda', '+595971000001', 'whatsapp',     900001, now()),
        ('Seed Excel',   '+595971000003', 'import:excel', NULL,   now() - interval '400 days')
       ) AS v(name, phone, source, property_id, created_at)
 WHERE NOT EXISTS (SELECT 1 FROM contacts c WHERE c.phone = v.phone);
