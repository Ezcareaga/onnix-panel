"""Regression — WebP MIME type registration.

Python's stdlib ``mimetypes`` module does not know about ``.webp`` on every
system. When Starlette's ``StaticFiles`` serves the property images mounted
at ``/images``, it relies on ``mimetypes.guess_type`` to set the
``Content-Type`` header. If the type comes back as ``None``, browsers can
refuse to render the file as an image.

In production, nginx serves ``/images/`` directly with the correct MIME, so
the bug only manifests in environments where requests are proxied straight
to FastAPI (e.g. staging/dev). The fix is to register ``image/webp`` once
during app import.
"""
from __future__ import annotations

import mimetypes


def test_webp_mimetype_is_registered():
    """``app.main`` must register ``image/webp`` so StaticFiles serves it correctly."""
    # Import for side-effect — ``app.main`` registers the MIME type at module load.
    import app.main  # noqa: F401

    mime, _ = mimetypes.guess_type("anything.webp")
    assert mime == "image/webp", (
        f"Expected webp to resolve to 'image/webp', got {mime!r}. "
        "Without this, /images/*.webp is served as text/plain in staging."
    )
