-- panel/scripts/m6_0_clean04_inactivate_ic_leftovers.sql
-- M6.0 / CLEAN-04: soft-delete 5 IC test-dedup leftovers sin URL
BEGIN;

UPDATE properties
   SET is_active = FALSE
 WHERE id IN (1013103, 1013104, 1013106, 1013108, 1013109)
   AND source = 'infocasas'
   AND (url IS NULL OR url = '');
-- Esperado: UPDATE 5

-- Verificación invariante:
SELECT COUNT(*) AS leftovers
  FROM properties
 WHERE source = 'infocasas'
   AND is_active = TRUE
   AND (url IS NULL OR url = '');
-- Esperado: 0

COMMIT;
