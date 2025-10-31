"""
Guideline retrieval via DuckDuckGo HTML (site-restricted), now:
- uses shared session with retries & (optional) cache
- respects robots.txt
- per-host rate limiting
- URL cleanup and basic validation
- same return schema as before
"""

from __future__ import annotations

import re
from typing import List, Dict, Optional
from urllib.parse import quote_plus

from common_http import get_http_session, safe_get, clean_url
from settings import DEFAULT_TIMEOUT_SEC

DDG_HTML = "https://duckduckgo.com/html/?q={query}&kl=us-en"

# very small allowlist for guideline org inference
ORG_MAP = {
    "who.int": "WHO",
    "cdc.gov": "CDC",
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


def _infer_org(url: str) -> Optional[str]:
    url = url or ""
    for dom, org in ORG_MAP.items():
        if dom in url.lower():
            return org
    return None


def _parse_ddg(html: str, max_results: int = 10) -> List[Dict]:
    """
    Lightweight parse of DDG HTML results.
    """
    out: List[Dict] = []
    # Each result item is in <a class="result__a" href="...">Title</a>
    for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        url = clean_url(m.group(1))
        # strip HTML tags in title
        title = re.sub("<.*?>", "", m.group(2)).strip()
        if not title or not url.startswith(("http://", "https://")):
            continue

        # snippet is in <a ...> then nearby <a class="result__snippet">...</a> or <a class="result__snippet js-result-snippet">...</a>
        # (DDG HTML changes occasionally; this is resilient enough.)
        start = max(m.start() - 500, 0)
        end = min(m.end() + 500, len(html))
        window = html[start:end]
        sm = re.search(r'<a[^>]*class="result__snippet[^"]*"[^>]*>(.*?)</a>', window, flags=re.I | re.S)
        snippet = re.sub("<.*?>", "", sm.group(1)).strip() if sm else ""

        out.append({
            "title": title,
            "url": url,
            "snippet": snippet,
        })
        if len(out) >= max_results:
            break
    return out


def _site_query(q: str, site: str) -> str:
    # prefer site-restricted queries
    return f'site:{site} {q}'.strip()


def fetch_guidelines_for_query(query: str, years: int = 5, max_results: int = 10) -> List[Dict]:
    """
    Returns list[dict]: {title, url, snippet, year, org}
    """
    session = get_http_session()

    results: List[Dict] = []
    for site in ("who.int", "cdc.gov"):
        q = _site_query(query, site)
        url = DDG_HTML.format(query=quote_plus(q))
        r = safe_get(url, timeout=DEFAULT_TIMEOUT_SEC, allow_non_200=False)
        if not r:
            continue

        items = _parse_ddg(r.text, max_results=max_results)
        for it in items:
            it["year"] = _guess_year(f'{it.get("title","")} {it.get("snippet","")}')
            it["org"] = _infer_org(it.get("url", ""))
            results.append(it)

    # dedupe by URL & title
    seen = set()
    deduped: List[Dict] = []
    for it in results:
        key = (it.get("url"), it.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # (light) sort: prefer org hits, then by year desc if present
    def _score(x: Dict) -> tuple:
        org_bonus = 0 if (x.get("org") in {"WHO", "CDC"}) else 1  # 0 is better
        year = x.get("year") or 0
        return (org_bonus, -year)

    deduped.sort(key=_score)
    return deduped[:max_results]
