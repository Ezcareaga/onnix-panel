-- Onnix SA — Initial Database Setup
-- This script runs automatically on first PostgreSQL container start.
-- It will NOT run again if the data volume already has data.

-- Enable extensions needed by the project
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Verify extensions are installed
DO $$
BEGIN
    RAISE NOTICE 'Extensions installed: unaccent, pg_trgm';
END
$$;
