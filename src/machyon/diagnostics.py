"""Human explanations for failures, and `machyon doctor URL` - a step-by-step connection check."""
from __future__ import annotations

import asyncio
import platform
import socket
import ssl
import sys
import time
import urllib.request

import aiohttp

from .utils import DEFAULT_USER_AGENT, sniff_image, split_host

HINTS = {
    "ssl_certificate": (
        "The server's TLS certificate could not be verified, so nothing was downloaded. Usual causes: "
        "antivirus/firewall HTTPS scanning or a corporate proxy re-signing traffic, a wrong system clock, "
        "or an outdated CA store. Run `machyon doctor <url>` to see which trust source works. "
        "Last resort, for hosts you trust: --insecure."),
    "ssl": "TLS handshake failed (connection reset or TLS alert). Usually transient; it was retried.",
    "connection": "Could not connect (DNS, firewall, proxy or the server is down). Check your network / HTTPS_PROXY.",
    "timeout": "The server was too slow. Raise --timeout, or lower --max-concurrency if your link is saturated.",
    "http_403": "The server refused access. Try --referer with the site's page URL, headers such as cookies, "
                "or slow down; you may be blocked or need to log in.",
    "http_404": "The image no longer exists at that URL.",
    "http_429": "Rate limited even after backing off. Re-run later or lower --max-concurrency.",
    "not_image": "The server answered with something that isn't an image (an HTML error, login or bot-check page).",
    "truncated": "The connection dropped mid-download; it was retried.",
}


def hint(reason: str) -> str | None:
    if reason in HINTS:
        return HINTS[reason]
    if reason.startswith("http_5"):
        return "Server error; it was retried. Re-run `machyon retry` later."
    if reason.startswith("write_error"):
        return "Could not write the file (permissions, file locked by another program, or disk problem)."
    return None


# ---------------------------------------------------------------------- doctor

async def _try(url: str, ctx, headers: dict, proxy: str | None) -> str:
    timeout = aiohttp.ClientTimeout(total=30, sock_connect=15, sock_read=15)
    connector = aiohttp.TCPConnector(ssl=ctx)
    t0 = time.perf_counter()
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as s, \
                s.get(url, proxy=proxy, allow_redirects=True) as r:
            body = await r.content.read(65536)
            fmt = sniff_image(body[:64])
            return (f"OK  HTTP {r.status} {r.reason}, {r.content_type}, "
                    f"{r.content_length if r.content_length is not None else '?'} bytes, "
                    f"{'image: ' + fmt if fmt else 'NOT an image'}  ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    except aiohttp.ClientConnectorCertificateError as e:
        return f"FAIL certificate: {getattr(e, 'certificate_error', e)}"
    except Exception as e:
        return f"FAIL {type(e).__name__}: {e}"


async def doctor(url: str, headers: dict | None = None, referer: str | None = None) -> int:
    from . import __version__
    from .session import build_ssl_context

    out = print
    out(f"machyon {__version__} | Python {sys.version.split()[0]} on {platform.platform()}")
    out(f"aiohttp {aiohttp.__version__} | {ssl.OPENSSL_VERSION}")
    for mod in ("truststore", "certifi", "uvloop", "winloop"):
        try:
            m = __import__(mod)
            out(f"  {mod:<10} {getattr(m, '__version__', 'installed')}")
        except ImportError:
            out(f"  {mod:<10} not installed")
    host = split_host(url)
    if not host:
        out(f"\n✗ Not an http(s) URL: {url}")
        return 2
    proxies = urllib.request.getproxies()
    proxy = proxies.get("https" if url.startswith("https") else "http")
    if proxy and urllib.request.proxy_bypass(host):
        proxy = None
    out(f"\nProxy for {host}: {proxy or 'none'}")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        out(f"DNS: {host} -> {', '.join(sorted({i[4][0] for i in infos})[:4])}")
    except Exception as e:
        out(f"DNS: FAIL {e}  -> check your internet connection / DNS")
        return 1
    h = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    if referer:
        h["Referer"] = referer
    h.update(headers or {})

    out("\nTLS trust sources (Machyon uses 'native' by default):")
    results = {}
    for mode in ("native", "certifi", "python"):
        results[mode] = await _try(url, build_ssl_context(mode), h, proxy)
        out(f"  {mode:<8} {results[mode]}")
    if url.startswith("https"):
        results["insecure"] = await _try(url, False, h, proxy)
        out(f"  {'no-verify':<8} {results['insecure']}  (diagnostic only)")

    out("\nVerdict:")
    if results["native"].startswith("OK"):
        if "NOT an image" in results["native"]:
            out("  Connection is fine, but the server didn't return an image (login wall, hotlink protection or"
                " bot check). Try --referer with the page the image appears on.")
        else:
            out("  ✓ This host works with Machyon's defaults.")
        return 0
    if any(results[m].startswith("OK") for m in ("certifi", "python")):
        good = next(m for m in ("certifi", "python") if results[m].startswith("OK"))
        out(f"  Your OS trust store rejects this host but '{good}' accepts it. Use: --tls-trust {good}")
        return 0
    if results.get("insecure", "").startswith("OK"):
        out("  Every trust source rejects the certificate, but the server answers without verification.\n"
            "  Something between you and the site is re-signing HTTPS (antivirus 'HTTPS scanning', a corporate\n"
            "  proxy or a captive portal), or your clock is wrong. Fix that (or add its CA to your OS store);\n"
            "  as a last resort for a host you trust: --insecure")
        return 1
    out("  The host can't be reached at all from this machine (see the errors above).")
    return 1
