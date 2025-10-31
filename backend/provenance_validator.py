"""
Provenance scoring now fetches pages through the shared HTTP utilities:
- shared session with retries/cache
- robots + rate limit
- URL cleanup
(We keep your existing scoring logic intact as much as possible.)
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from common_http import safe_get, clean_url
from settings import DEFAULT_TIMEOUT_SEC

# --- your existing reputation & feature heuristics (kept) ---
REPUTABLE_HINTS = {
    "who.int": 15,
    "cdc.gov": 15,
    "nih.gov": 12,
    "cochranelibrary.com": 12,
    "nejm.org": 12,
    "thelancet.com": 12,
    "bmj.com": 12,
    "jama-network.com": 12,
    "nature.com": 10,
    "sciencemag.org": 10,
    "ox.ac.uk": 8,
    "harvard.edu": 8,
    "stanford.edu": 8,
    "reuters.com": 6,
    "apnews.com": 6,
    "bbc.com": 6,
    "nytimes.com": 4,
    "theguardian.com": 4,
}

C2PA_RE = re.compile(r'c2pa|content authenticity', re.I)
DATE_RE = re.compile(r'\b(20\d{2}|19\d{2})\b')
AUTHOR_RE = re.compile(r'By\s+[A-Z][\w\-\.\s]{1,40}', re.I)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _fetch_text(url: str) -> str:
    r = safe_get(url, timeout=DEFAULT_TIMEOUT_SEC, allow_non_200=False)
    if not r:
        return ""
    # keep it light; large pages trimmed upstream by size guard
    content_type = r.headers.get("Content-Type", "").lower()
    if "html" not in content_type and "xml" not in content_type:
        return ""
    try:
        r.encoding = r.apparent_encoding or r.encoding
    except Exception:
        pass
    return (r.text or "")[:200_000]  # hard safety cap


def _score_item(it: Dict) -> Dict:
    url = clean_url(it.get("url", "") or "")
    dom = _domain(url)

    score = 0
    reasons: List[str] = []

    # domain prior
    if dom in REPUTABLE_HINTS:
        score += REPUTABLE_HINTS[dom]
        reasons.append(f"Reputable domain: {dom} (+{REPUTABLE_HINTS[dom]})")

    # fetch body (polite)
    body = _fetch_text(url)

    if body:
        # C2PA / authenticity markers
        if C2PA_RE.search(body):
            score += 6
            reasons.append("Claims content authenticity / C2PA (+6)")

        # author/date presence (very light-weight heuristics)
        if AUTHOR_RE.search(body):
            score += 3
            reasons.append("Author line detected (+3)")

        if DATE_RE.search(body):
            score += 3
            reasons.append("Likely publication date present (+3)")

        # outbound scholarly links (very light heuristic)
        if "doi.org" in body or "pubmed.ncbi.nlm.nih.gov" in body:
            score += 4
            reasons.append("Cites scholarly sources (+4)")

    # normalize & label
    label = "High" if score >= 18 else "Medium" if score >= 10 else "Low"
    out = dict(it)
    out.update({
        "reliability_score": score,
        "reliability_label": label,
        "reliability_reasons": reasons,
    })
    return out


def assess_items(items: List[Dict]) -> List[Dict]:
    return [_score_item(it) for it in (items or [])]
