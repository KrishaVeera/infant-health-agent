"""
Shared HTTP utilities:
- Session with retries, timeouts, optional requests_cache
- Simple per-host rate limiting
- robots.txt allow check with caching
- URL sanitization helpers
"""

from __future__ import annotations

import time
import threading
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib import robotparser

from settings import (
    ENABLE_HTTP_CACHE, CACHE_BACKEND_PATH, DEFAULT_TIMEOUT_SEC,
    TOTAL_RETRIES, BACKOFF_FACTOR, PER_HOST_MAX_RPS, RESPECT_ROBOTS,
    ROBOTS_TTL_SEC, USER_AGENT
)

# ---- optional caching ----
_CACHE_INSTALLED = False
try:
    import requests_cache  # type: ignore
    _CACHE_INSTALLED = True
except Exception:
    _CACHE_INSTALLED = False

_SESSION_LOCK = threading.Lock()
_SESSION: Optional[requests.Session] = None

# Per-host rate limiting (simple "min interval between requests" model)
_HOST_LOCKS: dict[str, threading.Lock] = {}
_LAST_HIT_TS: dict[str, float] = {}
_MIN_INTERVAL = 1.0 / max(PER_HOST_MAX_RPS, 0.0001)  # guard div by zero

def _install_retries(sess: requests.Session) -> None:
    retry = Retry(
        total=TOTAL_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)

def get_http_session() -> requests.Session:
    """
    Returns a shared session with retries, optional cache, and default headers.
    """
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION

        if ENABLE_HTTP_CACHE and _CACHE_INSTALLED:
            # sqlite cache at CACHE_BACKEND_PATH
            _SESSION = requests_cache.CachedSession(
                CACHE_BACKEND_PATH,
                allowable_methods=("GET", "HEAD"),
                expire_after=60 * 60 * 6,  # 6 hours
                stale_if_error=True,
            )
        else:
            _SESSION = requests.Session()

        _SESSION.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        _install_retries(_SESSION)
        return _SESSION

def _host_lock(host: str) -> threading.Lock:
    with _SESSION_LOCK:
        if host not in _HOST_LOCKS:
            _HOST_LOCKS[host] = threading.Lock()
        return _HOST_LOCKS[host]

def rate_limit(url: str) -> None:
    """
    Enforces a minimal interval between requests per host.
    """
    host = urlparse(url).netloc.lower()
    lock = _host_lock(host)
    with lock:
        last = _LAST_HIT_TS.get(host, 0.0)
        wait = _MIN_INTERVAL - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _LAST_HIT_TS[host] = time.time()

def clean_url(url: str) -> str:
    """
    Strip common tracking params (utm_*, fbclid, gclid, ref) for dedupe & privacy.
    """
    parsed = urlparse(url)
    query = [(k, v) for (k, v) in parse_qsl(parsed.query, keep_blank_values=True)
             if not (k.startswith("utm_") or k in {"fbclid", "gclid", "ref"})]
    new_query = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

@lru_cache(maxsize=512)
def _load_robot_parser(base_url: str) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    rp.set_url(base_url)
    try:
        rp.read()
    except Exception:
        # On failure, be conservative: disallow only if parser loads and says so
        pass
    return rp

@lru_cache(maxsize=2048)
def is_allowed_by_robots(url: str, user_agent: str = USER_AGENT) -> bool:
    if not RESPECT_ROBOTS:
        return True
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = _load_robot_parser(robots_url)
        # robotparser has no TTL; emulate by time-based cache busting via dummy arg
        # (we rely on lru_cache eviction over time as a pragmatic compromise).
        return rp.can_fetch(user_agent, url)
    except Exception:
        # On errors, default to allow (like most clients do), but you can flip this if desired.
        return True

def safe_get(url: str, timeout: float = DEFAULT_TIMEOUT_SEC, allow_non_200: bool = False) -> Optional[requests.Response]:
    """
    HEAD -> check content length -> GET. Returns response or None.
    Applies robots & rate limit.
    """
    if not url:
        return None

    session = get_http_session()
    url = clean_url(url)

    if not is_allowed_by_robots(url):
        return None

    rate_limit(url)

    try:
        # quick HEAD probe (not all servers support it gracefully; ignore failures)
        h = session.head(url, timeout=timeout, allow_redirects=True)
        # Guard against huge downloads when server advertises size
        clen = h.headers.get("Content-Length")
        if clen is not None and clen.isdigit():
            if int(clen) > 2_500_000:  # 2.5 MB cap to be polite
                return None
    except Exception:
        pass

    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if not allow_non_200 and r.status_code != 200:
            return None
        return r
    except Exception:
        return None
