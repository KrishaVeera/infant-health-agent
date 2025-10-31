"""
Blog/News retrieval via DuckDuckGo HTML, now with:
- shared session, retries, cache
- robots + per-host rate limit
- URL cleanup & validation
- light domain reputation biasing
"""

from __future__ import annotations

import re
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urlparse

from common_http import safe_get, clean_url
from settings import DEFAULT_TIMEOUT_SEC

DDG_HTML = "https://duckduckgo.com/html/?q={query}&kl=us-en"

REPUTABLE = {
    # health/research news and general reputable outlets (expand as you wish)
    "who.int", "cdc.gov", "nejm.org", "thelancet.com", "bmj.com", "jama-network.com",
    "nature.com", "sciencemag.org", "nih.gov", "cochranelibrary.com", "ox.ac.uk",
    "stanford.edu", "harvard.edu", "bbc.com", "reuters.com", "apnews.com", "nytimes.com",
    "washingtonpost.com", "theguardian.com", "statnews.com"
}

YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")


def _guess_year(text: str) -> Optional[int]:
    if not text:
        return None
    m = YEAR_RE.search(text)
    if not m:
        return None
    try:
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return y
    except Exception:
        pass
    return None


def _parse_ddg(html: str, max_results: int = 10) -> List[Dict]:
    out: List[Dict] = []
    for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        url = clean_url(m.group(1))
        title = re.sub("<.*?>", "", m.group(2)).strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        start = max(m.start() - 500, 0)
        end = min(m.end() + 500, len(html))
        window = html[start:end]
        sm = re.search(r'<a[^>]*class="result__snippet[^"]*"[^>]*>(.*?)</a>', window, flags=re.I | re.S)
        snippet = re.sub("<.*?>", "", sm.group(1)).strip() if sm else ""
        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= max_results:
            break
    return out


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def web_search_duckduckgo(query: str, max_results: int = 10) -> List[Dict]:
    """
    Returns list[dict]: {title, url, snippet, year, org}
    'org' is the registrable domain (host), used later by provenance scoring.
    """
    q = quote_plus(query)
    url = DDG_HTML.format(query=q)
    r = safe_get(url, timeout=DEFAULT_TIMEOUT_SEC, allow_non_200=False)
    if not r:
        return []

    items = _parse_ddg(r.text, max_results=max_results * 2)  # parse more, then filter

    # normalize fields
    results: List[Dict] = []
    for it in items:
        u = it.get("url", "")
        d = _domain(u)
        if not d:
            continue
        results.append({
            "title": it.get("title", "").strip(),
            "url": u,
            "snippet": it.get("snippet", "").strip(),
            "year": _guess_year(f'{it.get("title","")} {it.get("snippet","")}'),
            "org": d,
        })

    # dedupe by domain+title or url
    seen = set()
    deduped: List[Dict] = []
    for it in results:
        key = (it["url"], it["title"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # light scoring: prefer reputable domains, then newer years, then shorter URLs
    def _score(x: Dict) -> tuple:
        dom = x.get("org", "")
        rep_penalty = 0 if dom in REPUTABLE else 1  # 0 is better
        year = x.get("year") or 0
        url_len = len(x.get("url", ""))
        return (rep_penalty, -year, url_len)

    deduped.sort(key=_score)
    return deduped[:max_results]
