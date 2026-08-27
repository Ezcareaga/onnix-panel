"""Simulate IC lead scenarios for smoke testing.

Scenario 1: direct lead with property match → wa_tpl_ic_welcome_v2 (3 buttons)
Scenario 2: reenviado lead                  → wa_tpl_ic_reenviado_welcome (2 buttons)

Run ONE scenario at a time so contexts don't overwrite each other.

Usage (inside container):
  python3 simulate_ic_lead.py <phone> <scenario>
  scenario: 1 or 2 (default: 1)

Example:
  python3 simulate_ic_lead.py +595971788846 1
  python3 simulate_ic_lead.py +595971788846 2
"""
import asyncio
import sys
from datetime import datetime, timezone
import os

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))

from app.database import async_session_factory
from app.repositories.contact_repo import contact_repo
from app.repositories.property_repo import property_repo
from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.lead_parser import ParsedLead

# Real IC property that exists in BOTH properties AND infocasas_properties:
# ic_id=190841, infocasas_ref="GAB5DA", property_id=11783
# "Hermosa casa en venta Zona Sajonia, Asuncion, USD 92.292"
IC_REF = "GAB5DA"
PROPERTY_ID = 11783


async def upsert_contact(phone: str):
    async with async_session_factory() as session:
        contact = await contact_repo.get_by_phone(session, phone)
        if contact is None:
            from app.models import Contact
            contact = Contact(
                name="Ez Test IC",
                phone=phone,
                source="infocasas",
                status="new",
            )
            session.add(contact)
            await session.commit()
            await session.refresh(contact)
            print(f"[OK] Contact created: id={contact.id}")
        else:
            print(f"[OK] Contact reused: id={contact.id} status={contact.status}")
        return contact


async def scenario_1(phone: str) -> None:
    """Direct lead with property match → wa_tpl_ic_welcome_v2 (3 buttons).

    Uses IC_REF="GAB5DA" (infocasas_ref) so get_ic_by_ref finds the full
    IC property and _preload_search_context sets last_detalle_id correctly.
    """
    svc = InfocasasService(session_manager=None, notification_fetcher=None)

    async with async_session_factory() as session:
        prop = await property_repo.get_by_id(session, PROPERTY_ID)
    if prop is None:
        print(f"[ERROR] Property {PROPERTY_ID} not found in properties table")
        return

    matched_property = {
        "id": prop.id,
        "title": prop.title,
        "city": prop.city,
        "neighborhood": prop.neighborhood,
        "operation": prop.operation,
        "property_type": prop.property_type,
        "price_usd": float(prop.price_usd) if prop.price_usd else None,
    }
    print(f"[OK] Property: {prop.title} | {prop.city} | USD {prop.price_usd}")
    print(f"[OK] Using IC ref: {IC_REF} → property_id={PROPERTY_ID}")

    contact = await upsert_contact(phone)

    parsed = ParsedLead(
        consulta_id="SIM-DIRECT-001",
        name="Ez Test IC",
        phone=phone,
        email=None,
        message="Estoy interesado en la propiedad",
        consulta_date=datetime.now(timezone.utc),
        property_code=IC_REF,   # Real infocasas_ref — get_ic_by_ref will find it
        property_title=prop.title,
        listing_city=prop.city,
        has_whatsapp=True,
        is_reassigned=False,
    )

    print("\n=== SCENARIO 1: direct lead + property match ===")
    await svc._send_whatsapp_welcome(parsed, matched_property, contact.id)
    print("[OK] Sent — check WhatsApp for ic_welcome_v2 with 3 buttons: [Ver detalles] [Ver similares] [Hablar con asesor]")
    print(f"[INFO] VER_DETALLES should show property {PROPERTY_ID}: {prop.title}")


async def scenario_2(phone: str) -> None:
    """Reenviado lead → wa_tpl_ic_reenviado_welcome (2 buttons).

    Based on real DB inventory: Villa Morra has 31 deptos alquiler,
    median price Gs 4.800.000. Using Gs 5.000.000 → 3 dorms.
    Listing zone "Villa Morra" is a barrio of Asunción (not a city),
    so it must be stored as barrio in filtros, not ciudad.
    """
    svc = InfocasasService(session_manager=None, notification_fetcher=None)
    contact = await upsert_contact(phone)

    parsed = ParsedLead(
        consulta_id="SIM-REENVIADO-001",
        name="Ez Test IC",
        phone=phone,
        email=None,
        message=(
            "consulta reenviada\n"
            "Apartamento en Alquiler de 3 dorms. en Villa Morra, "
            "90 m² por Gs. 5.000.000"
        ),
        consulta_date=datetime.now(timezone.utc),
        property_code=None,
        property_title=None,
        listing_city="Villa Morra",
        has_whatsapp=True,
        is_reassigned=True,
        listing_type="departamento",
        listing_operation="alquiler",
        listing_bedrooms=3,
        listing_area_m2=90.0,
        listing_price=5_000_000,
        listing_currency="gs",
    )

    print("\n=== SCENARIO 2: reenviado lead (Villa Morra, barrio de Asunción) ===")
    await svc._send_whatsapp_reenviado_welcome(contact, parsed, None)
    print("[OK] Sent — check WhatsApp for ic_reenviado_welcome with 2 buttons: [Sí, mostrame] [Ahora no]")
    print("[INFO] SI_MOSTRAME_REENVIADO will search: depto alquiler Villa Morra Gs ≤6.5M, 3 dorms")
    print("       (barrio detected → stored as barrio=Villa Morra not ciudad=Villa Morra)")


if __name__ == "__main__":
    phone = sys.argv[1] if len(sys.argv) > 1 else "+595971788846"
    scenario = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    if scenario == 1:
        asyncio.run(scenario_1(phone))
    elif scenario == 2:
        asyncio.run(scenario_2(phone))
    else:
        print("Usage: simulate_ic_lead.py <phone> <1|2>")
