"""Abstract base class for channel senders.

Defines the contract that TelegramSender and WhatsAppSender must
implement. Each sender delivers a ChannelPayload to a target chat.

Plan 63-01: CHAN-01 Base sender interface.
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bot.core.types import ChannelPayload


class BaseSender(abc.ABC):
    """Abstract channel sender.

    Subclasses implement ``send()`` to deliver messages via their
    platform's API.  The method must never raise — failures are
    logged internally and signalled by returning ``False``.
    """

    @abc.abstractmethod
    async def send(self, payload: "ChannelPayload", chat_id: str) -> bool:
        """Send a payload to *chat_id*.

        Returns ``True`` on success, ``False`` on failure.
        Must never raise an exception.
        """
