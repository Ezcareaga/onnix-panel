-- ============================================================================
-- Onnix SA — Complete Database Schema
-- ============================================================================
-- Project: Onnix SA — Sistema de Automatizacion Inmobiliaria
-- Database: onnix_prod (PostgreSQL 16)
-- Purpose: Idempotent schema migration — safe to run multiple times.
--
-- Execution:
--   docker exec -i onnix-postgres psql -U onnix -d onnix_prod < scripts/schema.sql
--
-- Prerequisites:
--   - PostgreSQL 16 (pgvector image si se va a usar la búsqueda semántica)
--   - Nada más: las extensiones se crean acá abajo (ver SECTION 1).
--
-- Base limpia (dev/test), verificado 2026-07-27:
--   createdb …  &&  psql -f scripts/schema.sql
--   → la suite de tests/ corre en verde contra esa base, sin parches a mano.
--
-- ESTE ARCHIVO ES EL BASELINE — Alembic NO puede crear la base desde cero.
--   La cadena arranca en 001_add_m2a_columns, que asume las tablas base ya
--   creadas. Sobre una base vacía, `alembic upgrade head` muere en
--   `relation "contacts" does not exist` al crear lead_events. Orden correcto:
--     1. createdb + CREATE EXTENSION vector (si no, properties no se puede crear)
--     2. psql -f scripts/schema.sql
--     3. alembic upgrade head
--   Verificado en el rebuild del VPS, 2026-08-17.
--
-- RESTAURAR DESDE UN DUMP (lo normal en prod/staging) — usar pg_restore, NO
--   `pg_dump | psql`: desde PG 16.10 el dump trae un \restrict que deja a psql
--   en modo restringido, y ahí rechaza los backslash que siguen, incluido el
--   \. que cierra cada COPY. Los datos no entran y el error es confuso.
--     pg_restore --schema=public --no-owner --no-privileges -j 3 -d <db> <dump>
--
-- ALCANCE — leer antes de asumir que esto es un espejo de producción:
--   Este archivo describe el esquema que el CÓDIGO de este repo necesita.
--   Producción tiene objetos que nunca se versionaron y que acá se
--   reconstruyeron a partir de lo que el código usa (columnas de agente,
--   department, property_type_normalized). Cuando haya acceso al server,
--   reemplazar este archivo por un `pg_dump --schema-only` de staging es
--   estrictamente mejor que mantenerlo a mano.
-- ============================================================================


-- ============================================================================
-- SECTION 1: EXTENSIONS
-- ============================================================================

-- pgcrypto provides crypt() and gen_salt() for bcrypt password hashing
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- unaccent + pg_trgm: los necesitan f_unaccent() y los índices GIN trigram de
-- más abajo. Este archivo los daba por instalados por un init_db.sql "de Phase 1"
-- que NO está en el repo, así que sobre una base limpia fallaba en cadena:
-- sin unaccent no hay f_unaccent, y sin f_unaccent no anda el dedup ni la
-- búsqueda del bot. Van acá con IF NOT EXISTS — si el init viejo ya corrió, no
-- molesta.
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ============================================================================
-- SECTION 2: HELPER FUNCTIONS
-- ============================================================================

-- f_unaccent(text) — IMMUTABLE wrapper around unaccent()
-- Required for functional indexes (PostgreSQL requires IMMUTABLE functions in index expressions).
-- Uses fully-qualified schema path to avoid ambiguity with search_path.
CREATE OR REPLACE FUNCTION f_unaccent(text)
RETURNS text AS $$
  SELECT public.unaccent('public.unaccent', $1);
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;

-- trigger_set_updated_at() — Generic trigger function for auto-updating updated_at columns.
-- Attached to properties, contacts, users, and conversations tables.
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- prevent_baja_reversal() — BEFORE UPDATE trigger function for contacts.
-- Enforces 'baja' as a TERMINAL, IRREVERSIBLE state (WhatsApp compliance).
-- Es la versión post-migración 004 (opt_out → baja) y es la que usa el trigger
-- enforce_baja_terminal más abajo; faltaba en este archivo, así que sobre una
-- base limpia el CREATE TRIGGER fallaba con "function does not exist".
CREATE OR REPLACE FUNCTION prevent_baja_reversal()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status = 'baja' AND NEW.status != 'baja' THEN
    RAISE EXCEPTION 'Cannot reverse baja status. baja is irreversible (contact_id: %)', OLD.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- prevent_opt_out_reversal() — versión PRE-004, mantenida sólo porque la
-- migración 004 la recrea en su downgrade.
CREATE OR REPLACE FUNCTION prevent_opt_out_reversal()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status = 'opt_out' AND NEW.status != 'opt_out' THEN
    RAISE EXCEPTION 'Cannot reverse opt_out status. opt_out is irreversible (contact_id: %)', OLD.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- sync_baja_to_contacts() — AFTER INSERT trigger function on bajas.
-- When a phone is added to bajas, automatically updates the matching contact's
-- status to 'baja' and records the timestamp. Ensures contacts and bajas
-- tables stay in sync regardless of which code path inserts the baja.
CREATE OR REPLACE FUNCTION sync_baja_to_contacts()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE contacts
  SET status = 'baja',
      baja_at = NOW()
  WHERE phone = NEW.phone
    AND status != 'baja';
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- SECTION 3: TABLES (in FK dependency order)
-- ============================================================================

-- 3a. users — Must be created FIRST because contacts.assigned_to references users.id
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'user'
    CHECK (role IN ('admin', 'user')),
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3b. properties — Core table for all property listings from all sources.
-- Self-referencing FK (duplicate_of) for dedup chain.
-- NUMERIC(15,2) supports values up to 9,999,999,999,999.99 — sufficient for PYG amounts.
CREATE TABLE IF NOT EXISTS properties (
  id SERIAL PRIMARY KEY,
  source VARCHAR(20) NOT NULL,
  external_id VARCHAR(100) NOT NULL,
  title TEXT,
  description TEXT,
  url TEXT,

  -- Dual currency: always store both USD and PYG when possible
  price_usd NUMERIC(15,2),
  price_pyg NUMERIC(15,2),
  price_currency VARCHAR(3) DEFAULT 'USD',
  price_on_request BOOLEAN DEFAULT FALSE,

  -- Location
  city VARCHAR(100),
  neighborhood VARCHAR(100),
  address TEXT,
  latitude NUMERIC(10,7),
  longitude NUMERIC(10,7),

  -- Classification
  operation VARCHAR(20),
  property_type VARCHAR(50),

  -- Details
  bedrooms SMALLINT,
  bathrooms SMALLINT,
  parking_spaces SMALLINT,
  total_area_m2 NUMERIC(10,2),
  built_area_m2 NUMERIC(10,2),

  -- Images (array for multiple URLs, separate field for primary image)
  image_urls TEXT[],
  main_image_url TEXT,

  -- Deduplication: duplicate_of points to the canonical listing (el agregador tiene prioridad)
  duplicate_of INTEGER REFERENCES properties(id),
  is_active BOOLEAN DEFAULT TRUE,

  -- Datos del agente (C4) — los escriben los scrapers vía _UPSERT_COLUMNS.
  agent_name TEXT,
  agent_phone VARCHAR(50),
  agent_whatsapp VARCHAR(50),

  -- Departamento geográfico (C5).
  department VARCHAR(100),

  -- Tipo normalizado (FK lógica a property_types.id, migración 019).
  -- property_type_normalized NO va aca: la crea la migracion 019 junto con la
  -- tabla property_types, su FK y su indice parcial. Declararla tambien en
  -- este baseline hacia que la 019 muriera con
  --   column "property_type_normalized" of relation "properties" already exists
  -- y frenaba la cadena sobre una base recien armada.

  -- construction_state (033) y portal_listed_at / portal_expires_at (037)
  -- tampoco van acá, por lo mismo: las agregan sus migraciones y declararlas
  -- también en el baseline las hacía morir con "column ... already exists".
  -- Regla para este archivo: una columna que agrega una migración NO se
  -- escribe acá. El baseline es lo PRE-001, más los renames que ya trae
  -- adentro; todo lo demás lo pone la cadena.

  -- Raw scraped data preserved for debugging and re-processing
  raw_data JSONB,

  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  last_scraped_at TIMESTAMPTZ DEFAULT NOW(),

  -- Upsert constraint: one record per source+external_id combination
  CONSTRAINT uq_properties_source_external_id UNIQUE (source, external_id)
);

-- 3c. contacts — Leads imported from Excel or captured by bot.
-- Phone is nullable because some leads only have email.
CREATE TABLE IF NOT EXISTS contacts (
  id SERIAL PRIMARY KEY,
  phone VARCHAR(20),
  email VARCHAR(255),
  name TEXT,
  source VARCHAR(50),
  status VARCHAR(20) NOT NULL DEFAULT 'new'
    -- 'baja' reemplazó a 'opt_out' en la migración 004; este archivo se había
    -- quedado con el valor viejo mientras el trigger de abajo ya escribía 'baja'.
    CHECK (status IN ('new', 'contacted', 'hot', 'interview', 'cold', 'baja')),
  assigned_to INTEGER REFERENCES users(id),

  -- Interest profile: what the contact is looking for
  interest_operation VARCHAR(20),
  interest_type VARCHAR(50),
  interest_city VARCHAR(100),
  interest_min_price NUMERIC(15,2),
  interest_max_price NUMERIC(15,2),
  interest_bedrooms SMALLINT,

  -- Preserve full original import data (Excel columns, etc.)
  original_data JSONB,

  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  last_contact_at TIMESTAMPTZ,
  baja_at TIMESTAMPTZ,  -- era opted_out_at; renombrada en la migración 004

  -- Las tres columnas de abajo son de las que este archivo llama «objetos que
  -- producción tenía sin versionar»: ninguna migración las crea. La 014 dice
  -- literalmente «already applied» y su upgrade() es `pass`, así que sobre una
  -- base armada de cero no existían y el primer entrante moría con
  --   column "search_context" of relation "conversations" does not exist
  -- Se agregan acá, que es donde va lo pre-001.
  consulta_date TIMESTAMPTZ,   -- fecha de la consulta original del lead
  preferences JSONB
);

-- 3d. conversations — One conversation per contact interaction session.
CREATE TABLE IF NOT EXISTS conversations (
  id SERIAL PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  status VARCHAR(20) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'escalated', 'closed')),
  channel VARCHAR(20) NOT NULL DEFAULT 'whatsapp'
    CHECK (channel IN ('whatsapp', 'web', 'manual')),
  last_search_criteria JSONB,
  -- Sin versionar, como consulta_date y preferences. La escribe
  -- `ConversationManager` en cada entrante y la LEE el panel: el resumen de
  -- «qué buscaba» del lead sale de acá (`partials/lead_item.html`,
  -- `lead_service`, `dashboard_service`). Sin la columna no entra ni un
  -- mensaje: `persist_inbound` hace INSERT ... RETURNING search_context.
  search_context JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  last_message_at TIMESTAMPTZ
);

-- 3e. messages — Individual messages within conversations.
-- Messages are IMMUTABLE once created — no updated_at column.
CREATE TABLE IF NOT EXISTS messages (
  id SERIAL PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  direction VARCHAR(10) NOT NULL
    CHECK (direction IN ('inbound', 'outbound')),
  sender_type VARCHAR(10) NOT NULL
    CHECK (sender_type IN ('contact', 'bot', 'agent')),
  body TEXT NOT NULL,
  media_url TEXT,
  media_type VARCHAR(50),
  twilio_sid VARCHAR(50),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3f. exchange_rates — Daily USD/PYG exchange rate for dual-currency conversion.
CREATE TABLE IF NOT EXISTS exchange_rates (
  id SERIAL PRIMARY KEY,
  date DATE NOT NULL UNIQUE,
  usd_to_pyg NUMERIC(12,4) NOT NULL,
  source VARCHAR(50),
  fetched_at TIMESTAMPTZ,
  notes TEXT
);

-- 3g. bajas — Immutable list of phones that requested no contact.
-- APPEND-ONLY: no updated_at. Once a phone is here, it stays forever.
-- This is the authoritative source for baja status (WhatsApp compliance).
CREATE TABLE IF NOT EXISTS bajas (
  id SERIAL PRIMARY KEY,
  phone VARCHAR(50) NOT NULL UNIQUE,
  reason TEXT,
  source VARCHAR(50),
  created_at TIMESTAMPTZ DEFAULT NOW()
);


-- 3h. bot_errors — Errores del bot (webhook, timeouts de IA) para monitoreo,
-- circuit breaker y el reporte diario. La mapea `app/models/bot_error.py` y la
-- indexa la migración 029.
--
-- Faltaba en este archivo: era otro de los objetos que producción tenía sin
-- versionar. Sobre una base recién armada la 029 moría con
--   relation "bot_errors" does not exist
-- y el heartbeat no tenía dónde escribir.
--
-- `created_at` va TIMESTAMP **sin** zona a propósito. El modelo declara
-- `DateTime(timezone=True)` y miente; el esquema real es naive y guarda UTC.
-- `app/repositories/bot_error_repo.py:41` documenta la trampa y su consulta
-- depende de que la columna sea naive: marca el valor como UTC y recién
-- entonces lo pasa a hora de Asunción. Crearla con zona rompe ese corte.
CREATE TABLE IF NOT EXISTS bot_errors (
  id SERIAL PRIMARY KEY,
  workflow VARCHAR(100) NOT NULL,
  node VARCHAR(100),
  error_message TEXT,
  execution_id VARCHAR(50),
  chat_id VARCHAR(50),
  created_at TIMESTAMP DEFAULT NOW()
);


-- ============================================================================
-- SECTION 4: INDEXES
-- ============================================================================

-- Properties: accent-insensitive search indexes using f_unaccent() wrapper
CREATE INDEX IF NOT EXISTS idx_properties_city_unaccent
  ON properties (f_unaccent(lower(city)));

CREATE INDEX IF NOT EXISTS idx_properties_operation_unaccent
  ON properties (f_unaccent(lower(operation)));

CREATE INDEX IF NOT EXISTS idx_properties_type_unaccent
  ON properties (f_unaccent(lower(property_type)));

-- Properties: price index for range queries (only active, non-duplicate listings)
CREATE INDEX IF NOT EXISTS idx_properties_price_usd
  ON properties (price_usd)
  WHERE is_active = TRUE AND duplicate_of IS NULL;

-- Properties: composite index for the most common search pattern
CREATE INDEX IF NOT EXISTS idx_properties_active_search
  ON properties (is_active, duplicate_of, f_unaccent(lower(city)), f_unaccent(lower(operation)))
  WHERE is_active = TRUE AND duplicate_of IS NULL;

-- Properties: find all duplicates of a canonical listing
CREATE INDEX IF NOT EXISTS idx_properties_duplicate_of
  ON properties (duplicate_of)
  WHERE duplicate_of IS NOT NULL;

-- Properties: trigram index for fuzzy title search (e.g., "deprtamento" matches "departamento")
CREATE INDEX IF NOT EXISTS idx_properties_title_trgm
  ON properties USING GIN (f_unaccent(lower(title)) gin_trgm_ops);

-- Contacts: enforce unique phone numbers (partial — only when phone is not null)
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_phone_unique
  ON contacts (phone)
  WHERE phone IS NOT NULL;

-- Contacts: enforce unique email when no phone (fallback identifier)
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email_unique
  ON contacts (email)
  WHERE phone IS NULL AND email IS NOT NULL;

-- Contacts: filter by pipeline status (admin panel dashboards)
CREATE INDEX IF NOT EXISTS idx_contacts_status
  ON contacts (status);

-- Contacts: find contacts assigned to a specific agent
CREATE INDEX IF NOT EXISTS idx_contacts_assigned_to
  ON contacts (assigned_to)
  WHERE assigned_to IS NOT NULL;

-- Conversations: find all conversations for a contact
CREATE INDEX IF NOT EXISTS idx_conversations_contact_id
  ON conversations (contact_id);

-- Conversations: quickly find active conversations (bot processing)
CREATE INDEX IF NOT EXISTS idx_conversations_status
  ON conversations (status)
  WHERE status = 'active';

-- Messages: retrieve conversation history in chronological order
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
  ON messages (conversation_id, created_at);

-- Bajas: fast phone lookup before sending any message
-- (redundant with UNIQUE constraint but explicit for clarity and documentation)
CREATE INDEX IF NOT EXISTS idx_bajas_phone
  ON bajas (phone);

-- Exchange rates: get most recent rate quickly
CREATE INDEX IF NOT EXISTS idx_exchange_rates_date
  ON exchange_rates (date DESC);


-- ============================================================================
-- SECTION 5: TRIGGERS
-- ============================================================================
-- Using DO blocks with EXCEPTION handler for idempotency because
-- trigger creation does not support IF NOT EXISTS.

-- Auto-update updated_at on properties
DO $$ BEGIN
  CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON properties
    FOR EACH ROW
    EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Auto-update updated_at on contacts
DO $$ BEGIN
  CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON contacts
    FOR EACH ROW
    EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Auto-update updated_at on users
DO $$ BEGIN
  CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Auto-update updated_at on conversations
DO $$ BEGIN
  CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW
    EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Prevent baja status from being reversed (CRITICAL business rule)
DO $$ BEGIN
  CREATE TRIGGER enforce_baja_terminal
    BEFORE UPDATE ON contacts
    FOR EACH ROW
    EXECUTE FUNCTION prevent_baja_reversal();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Auto-sync baja status to contacts when phone added to bajas
DO $$ BEGIN
  CREATE TRIGGER sync_baja_status
    AFTER INSERT ON bajas
    FOR EACH ROW
    EXECUTE FUNCTION sync_baja_to_contacts();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ============================================================================
-- SECTION 6: SEARCH FUNCTION
-- ============================================================================

-- search_properties() — Main search function used by the WhatsApp bot.
-- Performs accent-insensitive, case-insensitive search across active, non-duplicate properties.
-- Onnix properties are prioritized in results (source = 'onnixpy' first).
-- Marked STABLE because it reads data but does not modify it.
CREATE OR REPLACE FUNCTION search_properties(
  p_city TEXT DEFAULT NULL,
  p_operation TEXT DEFAULT NULL,
  p_type TEXT DEFAULT NULL,
  p_min_price NUMERIC DEFAULT NULL,
  p_max_price NUMERIC DEFAULT NULL,
  p_bedrooms SMALLINT DEFAULT NULL,
  p_limit INTEGER DEFAULT 20
)
RETURNS SETOF properties AS $$
BEGIN
  RETURN QUERY
  SELECT *
  FROM properties
  WHERE is_active = TRUE
    AND duplicate_of IS NULL
    AND (p_city IS NULL OR f_unaccent(lower(city)) = f_unaccent(lower(p_city)))
    AND (p_operation IS NULL OR f_unaccent(lower(operation)) = f_unaccent(lower(p_operation)))
    AND (p_type IS NULL OR f_unaccent(lower(property_type)) = f_unaccent(lower(p_type)))
    AND (p_min_price IS NULL OR COALESCE(price_usd, 0) >= p_min_price)
    AND (p_max_price IS NULL OR COALESCE(price_usd, 0) <= p_max_price)
    AND (p_bedrooms IS NULL OR bedrooms >= p_bedrooms)
  ORDER BY
    CASE WHEN source = 'onnixpy' THEN 0 ELSE 1 END,
    created_at DESC
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;


-- ============================================================================
-- SECTION 7: SEED DATA
-- ============================================================================

-- IMPORTANT: Change this password on first login!
-- Default admin account for initial system access.
-- Password is hashed with bcrypt (cost factor 10) via pgcrypto.
INSERT INTO users (email, password_hash, name, role)
VALUES (
  'admin@onnix.com.py',
  crypt('OnnixAdmin2026!', gen_salt('bf', 10)),
  'Administrador',
  'admin'
)
ON CONFLICT (email) DO NOTHING;


-- ============================================================================
-- DONE
-- ============================================================================
-- Schema creation complete. Run verification:
--   docker exec -i onnix-postgres psql -U onnix -d onnix_prod -c "\dt"
