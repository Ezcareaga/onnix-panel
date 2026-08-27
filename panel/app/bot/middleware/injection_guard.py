"""Prompt injection guard — input sanitization + suspicious pattern detection.

Sanitizes user input before it reaches the AI pipeline:
- Truncates to MAX_LENGTH (500 chars)
- Strips unicode control characters
- Detects suspicious patterns (Spanish + English) and logs WARNING
- Tracks suspicious message rate per user (3+ in 5 min = ALERT)

IMPORTANT: Messages are NEVER blocked — only logged. Claude with a good
system prompt resists injection. False positives would block real users.

Plan 67-04: Prompt injection guardrails.
"""
from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_LENGTH = 500
SUSPICIOUS_WINDOW_SECONDS = 300  # 5 minutes
SUSPICIOUS_THRESHOLD = 3

# --- Control character pattern ---
# Strip C0 controls (U+0000-U+001F) except \n (0x0A) and \t (0x09),
# plus zero-width chars (U+200B-U+200F, U+FEFF, U+2060).
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\ufeff\u2060]"
)

# --- Suspicious patterns ---
# Each entry is a compiled regex. We use \b-less patterns because
# Spanish words often appear mid-sentence without clear boundaries.
_SUSPICIOUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignor\w*\s+.*instrucciones", re.IGNORECASE), "ignor+instrucciones"),
    (re.compile(r"system\s*prompt", re.IGNORECASE), "system prompt"),
    (re.compile(r"sos\s+ahora", re.IGNORECASE), "sos ahora"),
    (re.compile(r"ahora\s+sos", re.IGNORECASE), "ahora sos"),
    (re.compile(r"actua\s+como", re.IGNORECASE), "actua como"),
    (re.compile(r"forget.*instructions", re.IGNORECASE), "forget+instructions"),
    (re.compile(r"\bjailbreak\b", re.IGNORECASE), "jailbreak"),
    (re.compile(r"\bDAN\b"), "DAN"),
]


@dataclass
class SanitizeResult:
    """Result of input sanitization."""

    text: str
    is_suspicious: bool


class InjectionGuard:
    """Input sanitization + suspicious pattern detection middleware.

    Usage::

        guard = InjectionGuard()
        result = guard.sanitize(user_text)
        if result.is_suspicious:
            guard.record_suspicious(user_id)
        # Always use result.text (sanitized) for the AI pipeline
    """

    def __init__(self) -> None:
        self._suspicious_timestamps: dict[str, list[float]] = {}

    def sanitize(self, text: str | None) -> SanitizeResult:
        """Sanitize user input text.

        1. Convert None to empty string
        2. Strip control characters
        3. Truncate to MAX_LENGTH
        4. Detect suspicious patterns (log WARNING if found)

        Returns SanitizeResult with sanitized text and suspicious flag.
        Messages are NEVER blocked.
        """
        if text is None:
            return SanitizeResult(text="", is_suspicious=False)

        # Step 1: Strip control characters
        cleaned = _CONTROL_RE.sub("", text)

        # Step 2: Truncate
        if len(cleaned) > MAX_LENGTH:
            cleaned = cleaned[:MAX_LENGTH]

        # Step 3: Detect suspicious patterns
        is_suspicious = False
        for pattern, label in _SUSPICIOUS_PATTERNS:
            if pattern.search(cleaned):
                is_suspicious = True
                logger.warning(
                    'Suspicious input detected — {"pattern": "%s", "text_preview": "%.80s"}',
                    label,
                    cleaned,
                )
                break  # One match is enough

        return SanitizeResult(text=cleaned, is_suspicious=is_suspicious)

    def record_suspicious(self, user_id: str) -> None:
        """Record a suspicious message for rate tracking.

        If the user has sent 3+ suspicious messages in the last 5 minutes,
        log at ERROR level (ALERT). Does NOT block the user.
        """
        now = time.monotonic()
        cutoff = now - SUSPICIOUS_WINDOW_SECONDS

        if user_id not in self._suspicious_timestamps:
            self._suspicious_timestamps[user_id] = []

        # Prune expired timestamps
        self._suspicious_timestamps[user_id] = [
            ts for ts in self._suspicious_timestamps[user_id] if ts > cutoff
        ]

        # Record this one
        self._suspicious_timestamps[user_id].append(now)

        count = len(self._suspicious_timestamps[user_id])
        if count >= SUSPICIOUS_THRESHOLD:
            logger.error(
                'ALERT: Repeated injection attempts — {"user_id": "%s", "count": %d, "window": "%ds"}',
                user_id,
                count,
                SUSPICIOUS_WINDOW_SECONDS,
            )


# ---------------------------------------------------------------------------
# Tool output sanitization
# ---------------------------------------------------------------------------

_MAX_PROPERTIES_FOR_AI = 10
_MAX_DESCRIPTION_LENGTH = 200
_MAX_DETAIL_DESCRIPTION_LENGTH = 2000
_INTERNAL_FIELDS = {"external_id", "source", "duplicate_of", "image_urls"}


def sanitize_tool_output(result: dict) -> dict:
    """Sanitize tool output before passing back to Claude.

    Applied to search_properties and get_property_detail results:
    - Limit properties list to _MAX_PROPERTIES_FOR_AI
    - Truncate description to _MAX_DESCRIPTION_LENGTH
    - Strip internal fields (external_id, source, duplicate_of)

    Lead, error, and other results pass through unchanged.
    """
    # Handle flat property dict (from get_property_detail) — single property
    # gets a generous description limit so Claude can produce a good summary.
    if "properties" not in result and "id" in result and "description" in result:
        cleaned = {k: v for k, v in result.items() if k not in _INTERNAL_FIELDS}
        desc = cleaned.get("description", "")
        if isinstance(desc, str) and len(desc) > _MAX_DETAIL_DESCRIPTION_LENGTH:
            cleaned["description"] = desc[:_MAX_DETAIL_DESCRIPTION_LENGTH]
        return cleaned

    if "properties" not in result:
        return result

    properties = result["properties"][:_MAX_PROPERTIES_FOR_AI]

    sanitized_props = []
    for prop in properties:
        cleaned = {k: v for k, v in prop.items() if k not in _INTERNAL_FIELDS}
        desc = cleaned.get("description", "")
        if isinstance(desc, str) and len(desc) > _MAX_DESCRIPTION_LENGTH:
            cleaned["description"] = desc[:_MAX_DESCRIPTION_LENGTH]
        sanitized_props.append(cleaned)

    return {
        **result,
        "properties": sanitized_props,
    }
