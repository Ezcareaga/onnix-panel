"""Unified webhook router — combines Telegram and WhatsApp sub-routers.

Provides a single ``webhook_router`` that can be included in the
FastAPI app to register all bot webhook endpoints at once.

Plan 66-03: Task 4 — Unified router.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.bot.webhooks.telegram import router as telegram_router
from app.bot.webhooks.whatsapp import router as whatsapp_router

webhook_router = APIRouter()
webhook_router.include_router(telegram_router)
webhook_router.include_router(whatsapp_router)
