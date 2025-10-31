"""
Centralized toggles for HTTP behavior, caching, and crawl etiquette.
All values can be overridden by environment variables at runtime.
"""

import os

def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}

# ---- caching ----
ENABLE_HTTP_CACHE: bool = _get_bool("ENABLE_HTTP_CACHE", True)
CACHE_BACKEND_PATH: str = os.getenv("CACHE_BACKEND_PATH", ".http_cache")

# ---- network timeouts & retries ----
DEFAULT_TIMEOUT_SEC: float = float(os.getenv("DEFAULT_TIMEOUT_SEC", "12.0"))
TOTAL_RETRIES: int = int(os.getenv("TOTAL_RETRIES", "3"))
BACKOFF_FACTOR: float = float(os.getenv("BACKOFF_FACTOR", "0.5"))

# ---- rate limit (per host) ----
# e.g., 0.5 => at most one request every 2 seconds per host
PER_HOST_MAX_RPS: float = float(os.getenv("PER_HOST_MAX_RPS", "0.5"))

# ---- robots ----
RESPECT_ROBOTS: bool = _get_bool("RESPECT_ROBOTS", True)
ROBOTS_TTL_SEC: int = int(os.getenv("ROBOTS_TTL_SEC", "86400"))  # 1 day

# ---- user agent ----
USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "agentkit/0.3 (+https://example.com) requests"
)

# ---- safety ----
MAX_CONTENT_LENGTH_BYTES: int = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", str(2_500_000)))  # ~2.5MB
