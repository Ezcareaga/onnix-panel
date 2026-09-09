"""Unified webhook router.

Provides a single ``webhook_router`` that can be included in the
FastAPI app to register all inbound webhook endpoints at once.

Telegram se fue el 2026-09-09: no es uno de los tres canales del producto.
Instagram y Messenger entran por `/webhooks/meta`, uno solo para los dos.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.bot.webhooks.meta import router as meta_router
from app.bot.webhooks.whatsapp import router as whatsapp_router

webhook_router = APIRouter()
webhook_router.include_router(whatsapp_router)
webhook_router.include_router(meta_router)
