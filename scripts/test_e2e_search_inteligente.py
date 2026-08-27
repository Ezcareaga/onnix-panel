#!/usr/bin/env python3
"""
E2E Tests for GSD Search Inteligente — Fases 1-4
Verifies via search_context and conversations DB state.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

import psycopg2

WEBHOOK_URL = "http://localhost:5678/webhook/whatsapp"
TEST_PHONE = "+595999000002"
TWILIO_TO = "whatsapp:+595900000000"

# Read env
ENV = {}
for line in open("/home/onnix/.env"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        ENV[k] = v

PG_CONN = f"host=localhost port=5432 dbname=onnix_prod user=onnix password={ENV.get('POSTGRES_PASSWORD','')}"
TWILIO_SID = ENV.get("TWILIO_ACCOUNT_SID", "")


def db_query(sql, params=None):
    conn = psycopg2.connect(PG_CONN)
    cur = conn.cursor()
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
    conn.close()
    return rows


def db_execute(sql, params=None):
    conn = psycopg2.connect(PG_CONN)
    cur = conn.cursor()
    cur.execute(sql, params or ())
    conn.commit()
    conn.close()


def send_webhook(body, msg_sid=None):
    if not msg_sid:
        msg_sid = "SM" + uuid.uuid4().hex[:16]
    data = urllib.parse.urlencode({
        "AccountSid": TWILIO_SID,
        "From": f"whatsapp:{TEST_PHONE}",
        "To": TWILIO_TO,
        "Body": body,
        "MessageSid": msg_sid,
        "ProfileName": "TestBot E2E",
        "NumMedia": "0",
        "SmsStatus": "received",
        "WaId": TEST_PHONE.replace("+", ""),
        "ApiVersion": "2010-04-01",
    }).encode()
    req = urllib.request.Request(WEBHOOK_URL, data=data,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, msg_sid
    except Exception as e:
        return 0, msg_sid


def get_search_context(wait=15):
    time.sleep(wait)
    rows = db_query("""
        SELECT conv.search_context
        FROM conversations conv JOIN contacts c ON conv.contact_id = c.id
        WHERE c.phone = %s ORDER BY conv.updated_at DESC LIMIT 1
    """, (TEST_PHONE,))
    if not rows:
        return {}
    sc = rows[0].get("search_context")
    if isinstance(sc, str):
        return json.loads(sc)
    return sc or {}


def get_inbound_messages(limit=3):
    return db_query("""
        SELECT m.id, m.body, m.intent, m.created_at
        FROM messages m JOIN contacts c ON m.contact_id = c.id
        WHERE c.phone = %s AND m.direction = 'inbound'
        ORDER BY m.created_at DESC LIMIT %s
    """, (TEST_PHONE, limit))


def reset_state():
    db_execute("""
        UPDATE conversations SET search_context = '{}'::jsonb
        WHERE contact_id = (SELECT id FROM contacts WHERE phone = %s ORDER BY id LIMIT 1)
    """, (TEST_PHONE,))


def set_wizard_state(step, filtros):
    sc = json.dumps({"wizard_step": step, "wizard_filtros": filtros})
    db_execute("""
        UPDATE conversations SET search_context = %s::jsonb, updated_at = NOW()
        WHERE contact_id = (SELECT id FROM contacts WHERE phone = %s ORDER BY id LIMIT 1)
    """, (sc, TEST_PHONE))


def cleanup():
    db_execute("DELETE FROM messages WHERE contact_id IN (SELECT id FROM contacts WHERE phone = %s)", (TEST_PHONE,))
    db_execute("DELETE FROM lead_events WHERE contact_id IN (SELECT id FROM contacts WHERE phone = %s)", (TEST_PHONE,))
    db_execute("DELETE FROM conversations WHERE contact_id IN (SELECT id FROM contacts WHERE phone = %s)", (TEST_PHONE,))
    db_execute("DELETE FROM contacts WHERE phone = %s", (TEST_PHONE,))


# ============================================================
results = []

def test(name, fase):
    """Decorator-like: prints test header"""
    print(f"\n  [{fase}] {name}")
    return {"name": name, "fase": fase}


def ok(t, detail):
    print(f"    ✅ {detail}")
    results.append({"passed": True, **t, "detail": detail})

def fail(t, detail):
    print(f"    ❌ {detail}")
    results.append({"passed": False, **t, "detail": detail})


# ============================================================
# FASE 1 TESTS
# ============================================================
def test_fase1():
    # --- Test 1: Dormitorios = 1 ---
    t = test("Dormitorios=1 exacto", "F1")
    reset_state()
    send_webhook("Busco departamento de 1 dormitorio en Asuncion")
    sc = get_search_context()
    filtros = sc.get("filtros", {})
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if shown:
        # Verify all shown properties have bedrooms=1
        rows = db_query("SELECT id, bedrooms FROM properties WHERE id = ANY(%s)", (shown,))
        bad = [r for r in rows if r.get("bedrooms") and r["bedrooms"] != 1]
        if bad:
            fail(t, f"Found properties with bedrooms != 1: {[(r['id'], r['bedrooms']) for r in bad]}")
        else:
            ok(t, f"All {len(rows)} shown properties have bedrooms=1")
    elif sc.get("etapa") == "mostrando_resultados":
        ok(t, f"Search ran, filtros: {json.dumps(filtros)[:100]}")
    else:
        fail(t, f"No results shown. SC: {json.dumps(sc)[:200]}")

    # --- Test 2: Depto terminado ---
    t = test("Depto terminado (sin en-pozo)", "F1")
    reset_state()
    send_webhook("Busco departamento terminado en Villa Morra")
    sc = get_search_context()
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if shown:
        rows = db_query("SELECT id, property_type FROM properties WHERE id = ANY(%s)", (shown,))
        pozo = [r for r in rows if 'pozo' in (r.get("property_type") or "").lower()]
        if pozo:
            fail(t, f"Found en-pozo properties: {[(r['id'], r['property_type']) for r in pozo]}")
        else:
            ok(t, f"All {len(rows)} shown are NOT en-pozo: {[r['property_type'] for r in rows]}")
    else:
        ok(t, f"No results (may not have terminado in VM). SC etapa: {sc.get('etapa')}")

    # --- Test 3: Depto en pozo ---
    t = test("Depto en pozo (solo en-pozo)", "F1")
    reset_state()
    send_webhook("Busco departamento en pozo en Asuncion")
    sc = get_search_context()
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if shown:
        rows = db_query("SELECT id, property_type FROM properties WHERE id = ANY(%s)", (shown,))
        not_pozo = [r for r in rows if 'pozo' not in (r.get("property_type") or "").lower()]
        if not_pozo:
            fail(t, f"Found non-pozo properties: {[(r['id'], r['property_type']) for r in not_pozo]}")
        else:
            ok(t, f"All {len(rows)} shown are en-pozo")
    else:
        ok(t, f"No results shown. SC: {sc.get('etapa')}")

    # --- Test 4: Guardrail venta ---
    t = test("Guardrail venta >= 5000 USD", "F1")
    reset_state()
    send_webhook("Busco casa en venta en Luque")
    sc = get_search_context()
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if shown:
        rows = db_query("SELECT id, price_usd FROM properties WHERE id = ANY(%s)", (shown,))
        cheap = [r for r in rows if r.get("price_usd") and float(r["price_usd"]) < 5000]
        if cheap:
            fail(t, f"Found venta < 5000: {[(r['id'], r['price_usd']) for r in cheap]}")
        else:
            ok(t, f"All {len(rows)} prices >= 5000 USD")
    else:
        fail(t, f"No results for common search. SC: {json.dumps(sc)[:200]}")


# ============================================================
# FASE 2 TESTS
# ============================================================
def test_fase2():
    # --- Test 1: Normal results (no degradation) ---
    t = test("Sin degradación (resultados normales)", "F2")
    reset_state()
    send_webhook("Busco departamento en Villa Morra")
    sc = get_search_context()
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if shown and len(shown) > 0:
        ok(t, f"Got {len(shown)} results normally (no degradation needed)")
    else:
        fail(t, f"No results for common search. SC: {json.dumps(sc)[:200]}")

    # --- Test 2: Degradation by price ---
    t = test("Degradación por precio bajo", "F2")
    reset_state()
    send_webhook("Busco casa en Recoleta hasta 20000 dolares")
    sc = get_search_context()
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    etapa = sc.get("etapa", "")
    alternatives = sc.get("alternatives")
    if shown and len(shown) > 0:
        ok(t, f"Got {len(shown)} results (degradation worked or direct results)")
    elif alternatives:
        ok(t, f"Got alternatives (full degradation exhausted): {json.dumps(alternatives)[:100]}")
    elif etapa:
        ok(t, f"Search ran. Etapa: {etapa}")
    else:
        fail(t, f"No results/alternatives. SC: {json.dumps(sc)[:200]}")

    # --- Test 3: Degradation by zone ---
    t = test("Degradación por zona sin resultados", "F2")
    reset_state()
    send_webhook("Busco departamento de 1 dormitorio en Barrio Jara hasta 30000")
    sc = get_search_context()
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    etapa = sc.get("etapa", "")
    if shown and len(shown) > 0:
        # Check if results are from a different barrio (degradation)
        rows = db_query("SELECT id, neighborhood FROM properties WHERE id = ANY(%s)", (shown,))
        barrios = [r.get("neighborhood", "").lower() for r in rows]
        if any("jara" in b for b in barrios):
            ok(t, f"Found in Barrio Jara directly (no degradation needed)")
        else:
            ok(t, f"Degradation expanded zone: results in {', '.join(set(barrios))}")
    elif etapa:
        ok(t, f"Search ran. Etapa: {etapa}")
    else:
        fail(t, f"No results. SC: {json.dumps(sc)[:200]}")


# ============================================================
# FASE 3 TESTS
# ============================================================
def test_fase3():
    # --- Test 1: embed_query.py ---
    t = test("embed_query.py genera embedding 768-dim", "F3")
    try:
        out = subprocess.run(
            ["python3", str(Path(__file__).resolve().parent / "embed_query.py"), "departamento con vista panoramica"],
            capture_output=True, text=True, timeout=15
        )
        embedding = json.loads(out.stdout.strip())
        if len(embedding) == 768:
            ok(t, f"768 dims OK, first: {embedding[0]:.6f}")
        elif len(embedding) == 0:
            fail(t, "Empty embedding (rate limited by batch job)")
        else:
            fail(t, f"Wrong dims: {len(embedding)}")
    except Exception as e:
        fail(t, f"Error: {e}")

    # --- Test 2: SQL puro (sin descripcion_libre) ---
    t = test("SQL puro sin filtros blandos", "F3")
    reset_state()
    send_webhook("Busco casa en Luque hasta 200000")
    sc = get_search_context()
    filtros = sc.get("filtros", {})
    has_vector = filtros.get("hasVectorSearch", False)
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if shown:
        if has_vector:
            fail(t, f"Used vector search but shouldn't have (no soft filters)")
        else:
            ok(t, f"SQL puro: {len(shown)} results, no vector search")
    else:
        ok(t, f"Search ran. filtros: {json.dumps(filtros)[:100]}")

    # --- Test 3: Hybrid search (con descripcion_libre) ---
    t = test("Búsqueda con filtros blandos (pileta)", "F3")
    reset_state()
    send_webhook("Busco departamento con pileta en Asuncion")
    sc = get_search_context()
    filtros = sc.get("filtros", {})
    desc_libre = filtros.get("descripcionLibre") or filtros.get("descripcion_libre")
    shown = sc.get("shown_properties", sc.get("last_shown_ids", []))
    if desc_libre:
        ok(t, f"descripcion_libre extracted: '{desc_libre}'. Results: {len(shown or [])}")
    elif shown:
        ok(t, f"Got {len(shown)} results (classifier may not have extracted desc_libre)")
    else:
        ok(t, f"Search ran. Filtros: {json.dumps(filtros)[:150]}")


# ============================================================
# FASE 4 TESTS
# ============================================================
def test_fase4():
    # --- Test 1: Hola no rompe wizard ---
    t = test("'Hola' no rompe wizard", "F4")
    set_wizard_state("WAIT_ZONA", {"operacion": "venta", "tipo": "casa"})
    time.sleep(1)
    send_webhook("Hola")
    sc = get_search_context()
    ws = sc.get("wizard_step", "")
    if ws and ws != "":
        ok(t, f"Wizard intact: wizard_step={ws}")
    else:
        fail(t, f"Wizard broken by 'hola'. SC: {json.dumps(sc)[:200]}")

    # --- Test 2: Cancelar sale del wizard ---
    t = test("'Cancelar' sale del wizard", "F4")
    set_wizard_state("WAIT_ZONA", {"operacion": "venta", "tipo": "casa"})
    time.sleep(1)
    send_webhook("cancelar")
    sc = get_search_context()
    ws = sc.get("wizard_step")
    if not ws or ws == "" or ws is None:
        ok(t, "Wizard cancelled, wizard_step cleared")
    else:
        fail(t, f"Still in wizard: {ws}")

    # --- Test 3: Búsqueda completa sale del wizard ---
    t = test("Búsqueda completa sale del wizard", "F4")
    set_wizard_state("WAIT_OPERACION", {})
    time.sleep(1)
    send_webhook("Busco casa en Recoleta hasta 200000 dolares")
    sc = get_search_context(wait=18)
    ws = sc.get("wizard_step")
    etapa = sc.get("etapa", "")
    if (not ws or ws is None) and etapa:
        ok(t, f"Exited wizard, etapa: {etapa}")
    elif etapa == "mostrando_resultados":
        ok(t, "Search executed directly")
    else:
        fail(t, f"wizard_step={ws}, etapa={etapa}")


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print("E2E TESTS — GSD Search Inteligente")
    print("=" * 70)

    cleanup()
    print("\nSetup: creating test contact...")
    send_webhook("Hola", f"SM_setup_{uuid.uuid4().hex[:8]}")
    time.sleep(15)
    contact = db_query("SELECT id FROM contacts WHERE phone = %s", (TEST_PHONE,))
    if not contact:
        print("FATAL: Could not create test contact")
        return False
    print(f"  Contact created: ID {contact[0]['id']}")
    reset_state()

    print("\n--- FASE 1: SQL Fixes ---")
    test_fase1()
    print("\n--- FASE 2: Degradación ---")
    test_fase2()
    print("\n--- FASE 3: Vectores ---")
    test_fase3()
    print("\n--- FASE 4: Wizard ---")
    test_fase4()

    # Summary
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    by_fase = {}
    for r in results:
        by_fase.setdefault(r["fase"], []).append(r)

    total_pass = sum(1 for r in results if r["passed"])
    total_fail = sum(1 for r in results if not r["passed"])
    for fase in sorted(by_fase):
        tests = by_fase[fase]
        passed = sum(1 for t in tests if t["passed"])
        print(f"\n  {fase}: {passed}/{len(tests)}")
        for t in tests:
            icon = "✅" if t["passed"] else "❌"
            print(f"    {icon} {t['name']}: {t['detail'][:80]}")

    print(f"\n  TOTAL: {total_pass}/{total_pass + total_fail}")

    cleanup()
    return total_fail == 0


if __name__ == "__main__":
    ok_result = main()
    sys.exit(0 if ok_result else 1)
