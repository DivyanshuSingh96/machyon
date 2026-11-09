"""NetworkSessionManager: one pooled HTTP session, TLS trust, headers, per-host proxy resolution."""
from __future__ import annotations

import logging
import ssl
import urllib.request

import aiohttp

from .config import DownloadConfig

logger = logging.getLogger("machyon.session")

TLS_TRUST_MODES = ("native", "certifi", "python")


def build_ssl_context(trust: str = "native"):
    """SSL context that verifies certificates the way your browser does.

    native  - the OS verifier via `truststore` (Windows CryptoAPI / macOS Security / Linux OpenSSL
              store). Fetches missing intermediates and Windows' on-demand root certificates,
              and trusts corporate / antivirus CAs installed in the OS. Falls back to certifi.
    certifi - Mozilla's CA bundle (what `requests` uses), plus Python's defaults.
    python  - Python's defaults only. On Windows this only knows roots the OS has already cached,
              the classic cause of "CERTIFICATE_VERIFY_FAILED" for sites a browser opens fine.
    """
    if trust == "native":
        try:
            import truststore

            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception as e:  # truststore missing or unusable on this platform
            logger.debug(f"truststore unavailable ({e}); using certifi")
            trust = "certifi"
    ctx = ssl.create_default_context()  # honours SSL_CERT_FILE / SSL_CERT_DIR
    if trust == "certifi":
        try:
            import certifi

            ctx.load_verify_locations(cafile=certifi.where())
        except Exception as e:
            logger.debug(f"certifi unavailable ({e}); using Python defaults")
    return ctx


class NetworkSessionManager:
    """Owns the aiohttp session and connection pool.

    Proxies come from ``HTTP(S)_PROXY`` / ``NO_PROXY`` (and the OS settings on Windows/macOS),
    resolved once per host. aiohttp's own ``trust_env=True`` re-scans the environment and
    re-reads ``~/.netrc`` on every request - about a third of client CPU in profiling.
    """

    def __init__(self, config: DownloadConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._proxies: dict[str, str] = {}
        self._proxy_cache: dict[str, str | None] = {}

    async def get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            c = self.config
            connector = aiohttp.TCPConnector(limit=c.max_concurrency, limit_per_host=0, ttl_dns_cache=600,
                                             keepalive_timeout=75,
                                             ssl=build_ssl_context(c.tls_trust) if c.verify_ssl else False)
            headers = {"User-Agent": c.user_agent,
                       "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                       "Accept-Language": "en-US,en;q=0.9"}
            if not c.use_compression:
                headers["Accept-Encoding"] = "identity"
            if c.referer:
                headers["Referer"] = c.referer
            headers.update(c.headers)
            self._proxies = {k: v for k, v in urllib.request.getproxies().items() if k in ("http", "https")}
            self._proxy_cache = {}
            self._session = aiohttp.ClientSession(
                connector=connector, headers=headers, trust_env=False, read_bufsize=2 ** 18,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=c.timeout, sock_read=c.timeout),
            )
        return self._session

    def proxy_for(self, host: str, url: str) -> str | None:
        try:
            return self._proxy_cache[host]
        except KeyError:
            pass
        proxy = None
        if self._proxies:
            proxy = self._proxies.get("https" if url.startswith("https:") else "http")
            if proxy and urllib.request.proxy_bypass(host):
                proxy = None
        self._proxy_cache[host] = proxy
        return proxy

    async def reset(self) -> None:
        """Drop the session so the next get() rebuilds it (after header/referer changes)."""
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
