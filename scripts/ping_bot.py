#!/usr/bin/env python3
"""
Onnix SA — Bot Synthetic Ping
Envía "Hola" al bot y verifica que responde. Corre cada hora en horario activo PY.
Si no responde → escribe WARN en system.log y sale con exit 1.
Si hay error de conexión Telethon → escribe ERROR en system.log y sale con exit 0
  (problema del ping, no del bot — no genera falsa alerta en health_check).
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv('/home/onnix/.env')

PROJECT_DIR = '/home/onnix'
LOG_FILE    = f'{PROJECT_DIR}/logs/system/system.log'
API_ID      = int(os.getenv('TG_API_ID', '0'))
API_HASH    = os.getenv('TG_API_HASH', '')
BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', 'onnix_bot')
SESSION_NAME = os.getenv('TG_SESSION', f'{PROJECT_DIR}/.tg_session')
TIMEOUT     = 30  # segundos esperando respuesta


def _log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    line = f'[{ts}] [{level}] [PING] {msg}'
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def _in_active_hours() -> bool:
    """True si la hora actual en Paraguay (UTC-3) está entre 8:00 y 22:00."""
    now_py = datetime.now(timezone(timedelta(hours=-3)))
    return 8 <= now_py.hour < 22


async def ping() -> bool:
    """Envía "Hola" al bot y espera respuesta. Devuelve True si respondió."""
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            _log('ERROR', 'Sesión Telethon no autorizada — no se puede hacer ping (no alertar)')
            return True  # exit 0: problema del ping, no del bot

        bot = await client.get_entity(BOT_USERNAME)
        sent_time = datetime.now(timezone.utc).timestamp()

        await client.send_message(bot, 'Hola')
        await asyncio.sleep(3)

        elapsed = 3
        while elapsed < TIMEOUT:
            msgs = await client.get_messages(bot, limit=3)
            for msg in msgs:
                if not msg.out and msg.date.timestamp() > sent_time:
                    return True  # respondió
            await asyncio.sleep(2)
            elapsed += 2

        return False  # timeout sin respuesta

    except (ConnectionError, OSError) as e:
        _log('ERROR', f'Error de conexión Telethon: {e} — skip ping')
        return True  # exit 0: problema de red/ping, no del bot
    except Exception as e:
        _log('ERROR', f'Error inesperado en ping: {e} — skip ping')
        return True  # exit 0: problema del ping, no del bot
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main() -> None:
    if not _in_active_hours():
        sys.exit(0)

    responded = await ping()

    if responded:
        sys.exit(0)
    else:
        _log('WARN', 'Bot no respondió al ping')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
