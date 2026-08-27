-- M5 Intelligent Search — Metrics Dashboard
-- Run against onnix_dev (staging) or onnix_prod (production):
--   docker exec -i onnix-postgres psql -U onnix -d onnix_dev < scripts/m5_metrics.sql
--
-- Go/no-go threshold (Fase K): accepted / offered >= 40 % over 48 h of staging.

-- ============================================================
-- Query 1: Daily acceptance rate — last 14 days
-- Purpose: track offered/accepted/abandoned counts day-by-day
--          and compute the acceptance percentage used for the
--          Fase K go/no-go decision.
-- ============================================================
SELECT
  DATE_TRUNC('day', created_at) AS day,
  COUNT(*) FILTER (WHERE event_type = 'zero_results_offered')   AS offered,
  COUNT(*) FILTER (WHERE event_type = 'zero_results_accepted')  AS accepted,
  COUNT(*) FILTER (WHERE event_type = 'zero_results_abandoned') AS abandoned,
  ROUND(
    100.0
    * COUNT(*) FILTER (WHERE event_type = 'zero_results_accepted')
    / NULLIF(COUNT(*) FILTER (WHERE event_type = 'zero_results_offered'), 0),
    1
  ) AS accept_pct
FROM lead_events
WHERE event_type IN (
    'zero_results_offered',
    'zero_results_accepted',
    'zero_results_abandoned'
  )
  AND created_at >= NOW() - INTERVAL '14 days'
GROUP BY 1
ORDER BY 1 DESC;


-- ============================================================
-- Query 2: Acceptance by trigger type (callback vs text)
-- Purpose: understand which UX path drives acceptance —
--          callback (button tap) vs text (user typed it).
-- ============================================================
SELECT
  metadata->>'trigger' AS trigger_type,
  COUNT(*)             AS accepted
FROM lead_events
WHERE event_type = 'zero_results_accepted'
  AND created_at >= NOW() - INTERVAL '14 days'
GROUP BY 1;


-- ============================================================
-- Query 3: Most-accepted alternatives — last 14 days
-- Purpose: identify which alternative IDs (zona_vecina:lambare,
--          presupuesto_20pct, etc.) users click most often,
--          for tuning AlternativesBuilder priority rules.
-- ============================================================
SELECT
  metadata->>'alt_id' AS alt_id,
  COUNT(*)            AS accepted
FROM lead_events
WHERE event_type = 'zero_results_accepted'
  AND created_at >= NOW() - INTERVAL '14 days'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 20;
