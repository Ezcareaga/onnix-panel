#!/usr/bin/env python3
"""One-shot migration: move preferences['notas'] -> contact_notes table.

Idempotent: skips contacts where a note with identical content already exists
(predicate: contact_id = X AND content = Y AND user_id IS NULL).

Usage:
    docker exec onnix-panel-dev python /app/scripts/migrate_notes_from_preferences.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


async def run() -> None:
    """Run the migration."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        rows = await db.execute(
            text(
                "SELECT id, preferences->>'notas' AS notas, created_at "
                "FROM contacts "
                "WHERE preferences->>'notas' IS NOT NULL AND preferences->>'notas' != ''"
            )
        )
        contacts = rows.mappings().all()

        migrated = 0
        skipped = 0
        descartadas = 0
        for row in contacts:
            content: str = row["notas"]
            contact_id: int = row["id"]
            created_at = row["created_at"]

            # Skip dict-format values (old Claude-generated JSON like
            # {"urgencia":"alta","zona_inicial":"..."}) — clean up the key
            # but do NOT insert them as notes.
            if content.strip().startswith("{"):
                await db.execute(
                    text(
                        "UPDATE contacts SET preferences = preferences - 'notas' "
                        "WHERE id = :cid"
                    ),
                    {"cid": contact_id},
                )
                descartadas += 1
                continue

            exists = await db.execute(
                text(
                    "SELECT 1 FROM contact_notes "
                    "WHERE contact_id = :cid AND content = :content AND user_id IS NULL "
                    "LIMIT 1"
                ),
                {"cid": contact_id, "content": content},
            )
            if exists.scalar_one_or_none():
                skipped += 1
                continue

            await db.execute(
                text(
                    "INSERT INTO contact_notes "
                    "(contact_id, user_id, content, created_at, updated_at) "
                    "VALUES (:cid, NULL, :content, :created_at, :created_at)"
                ),
                {"cid": contact_id, "content": content, "created_at": created_at},
            )

            await db.execute(
                text(
                    "UPDATE contacts SET preferences = preferences - 'notas' "
                    "WHERE id = :cid"
                ),
                {"cid": contact_id},
            )

            migrated += 1

        await db.commit()
        print(
            f"Migrados: {migrated} | Descartadas: {descartadas} | "
            f"Ya existian: {skipped} | Total procesados: {len(contacts)}"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
