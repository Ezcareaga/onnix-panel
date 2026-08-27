"""Content-Security-Policy header assertions.

The panel is served behind Cloudflare with Web Analytics enabled. Cloudflare
injects a loader script from `static.cloudflareinsights.com` (the
"beacon.min.js"). Without that host listed in `script-src`, browsers block
the loader and the console fills with CSP violation reports.

This test pins the contract: the CSP header emitted by the
`security_headers` middleware (panel/app/main.py:84-91) MUST list
`static.cloudflareinsights.com` in the `script-src` directive.
"""
import pytest


@pytest.mark.asyncio
async def test_csp_header_includes_cloudflare_insights(client):
    """script-src must allow static.cloudflareinsights.com (Cloudflare beacon)."""
    response = await client.get("/login")

    csp = response.headers.get("Content-Security-Policy", "")
    assert csp, "Content-Security-Policy header missing from response"

    # The host must appear in the CSP at all.
    assert "static.cloudflareinsights.com" in csp, (
        f"Expected static.cloudflareinsights.com in CSP, got: {csp!r}"
    )

    # And specifically inside the script-src directive (not some unrelated one).
    script_src = next(
        (s.strip() for s in csp.split(";") if s.strip().startswith("script-src")),
        "",
    )
    assert script_src, f"script-src directive missing from CSP: {csp!r}"
    assert "static.cloudflareinsights.com" in script_src, (
        f"static.cloudflareinsights.com not in script-src directive: {script_src!r}"
    )
