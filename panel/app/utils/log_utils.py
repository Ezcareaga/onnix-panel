"""Logging utilities for Onnix SA panel.

Provides helpers to sanitize PII from log output without losing debuggability.
"""
from __future__ import annotations


def mask_email(email: str | None) -> str:
    """Return a non-reversible but debuggable masked form of an email address.

    Format: <first_char_local>***@<first_char_domain>***.<tld>

    Examples:
        juan@example.com          -> j***@e***.com
        admin@onnix.com.py -> a***@c***.py
        ez@onnix.com.py   -> e***@c***.py

    Edge cases handled without raising:
        None        -> "***"
        ""          -> "***"
        "notanemail" (no @) -> "n***"
        "@"         -> "***@***"
    """
    if not email:
        return "***"

    if "@" not in email:
        # Malformed — mask but keep first char for debugging
        return f"{email[0]}***" if email else "***"

    local, _, domain = email.partition("@")

    masked_local = f"{local[0]}***" if local else "***"

    if not domain:
        return f"{masked_local}@***"

    # Split domain into labels; keep first char of first label + TLD
    parts = domain.split(".")
    if len(parts) == 1:
        # No dot in domain (unusual but safe)
        masked_domain = f"{parts[0][0]}***" if parts[0] else "***"
        return f"{masked_local}@{masked_domain}"

    first_label = parts[0]
    tld = parts[-1]
    masked_first = f"{first_label[0]}***" if first_label else "***"
    return f"{masked_local}@{masked_first}.{tld}"
