# backend/provenance_validator.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests, json, re

UA = "infant-health-agent/0.1 (+https://example.com)"
GOOD_DOMAINS = {
    # gov/edu + selected reputable media/journals/NGOs (extend as needed)
    "who.int","cdc.gov","nih.gov","unicef.org","bmj.com","nature.com","nejm.org",
    "thelancet.com","sciencedirect.com","jamanetwork.com","springer.com","wiley.com",
    "oup.com","tandfonline.com","reuters.com","apnews.com","nytimes.com","bbc.com",
    "theguardian.com","gov","edu"
}

def _domain(url: str | None) -> str:
    if not url: return ""
    try: return urlparse(url).netloc.lower()
    except Exception: return ""

def _fetch_html(url: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.ok: return r.text[:500_000]  # cap
    except Exception:
        return ""
    return ""

def _has_c2pa(html: str) -> bool:
    return ('rel="c2pa"' in html) or ("application/c2pa" in html) or ("contentauth" in html)

def _extract_schema_meta(html: str) -> Tuple[str|None, str|None]:
    """Return (author, datePublished) if present in JSON-LD or meta tags."""
    author = None
    date = None
    # Try JSON-LD blocks
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        blocks = data if isinstance(data, list) else [data]
        for b in blocks:
            t = b.get("@type", "")
            if isinstance(t, list): t = " ".join(t)
            if "Article" in str(t):
                if not author and b.get("author"):
                    a = b["author"]
                    if isinstance(a, list) and a and isinstance(a[0], dict):
                        author = a[0].get("name")
                    elif isinstance(a, dict):
                        author = a.get("name")
                    elif isinstance(a, str):
                        author = a
                if not date:
                    date = b.get("datePublished") or b.get("dateModified")
    # Fallback simple meta
    if not author:
        m = re.search(r'(?:name|property)="author"\s+content="([^"]+)"', html, re.I)
        author = m.group(1) if m else None
    if not date:
        m = re.search(r'(?:name|property)="datePublished"\s+content="([^"]+)"', html, re.I)
        date = m.group(1) if m else None
    return author, date

def _outbound_link_score(html: str) -> float:
    # crude ratio of outbound links to reputable domains
    soup = BeautifulSoup(html, "html.parser")
    hrefs = [a.get("href","") for a in soup.find_all("a")]
    if not hrefs: return 0.0
    good = 0
    for h in hrefs:
        d = _domain(h)
        if not d: continue
        if d.endswith(".gov") or d.endswith(".edu") or d in GOOD_DOMAINS:
            good += 1
    return min(1.0, good / max(1, len(hrefs)))

def _corroborate_claim(title_or_snippet: str) -> bool:
    """VERY light corroboration: search the phrase; if we find another reputable domain hit, return True."""
    try:
        q = " ".join(title_or_snippet.split()[:8])  # shorten query
        r = requests.get("https://duckduckgo.com/html/", params={"q": q}, headers={"User-Agent": UA}, timeout=15)
        if not r.ok: return False
        soup = BeautifulSoup(r.text, "html.parser")
        hits = 0
        for a in soup.select(".result__a"):
            d = _domain(a.get("href") or "")
            if not d: continue
            if d.endswith(".gov") or d.endswith(".edu") or d in GOOD_DOMAINS:
                hits += 1
            if hits >= 2:
                return True
    except Exception:
        return False
    return False

def reliability_score(item: Dict[str, Any]) -> Tuple[int, str, List[str]]:
    """
    Returns: (score 0-100, label High/Medium/Low, reasons[])
    Works for web/guideline/paper items alike (uses URL + snippet/title).
    """
    score = 0
    reasons: List[str] = []
    url = item.get("url")
    html = _fetch_html(url) if url and url.startswith("http") else ""

    d = _domain(url)
    if d.endswith(".gov") or d.endswith(".edu") or d in GOOD_DOMAINS:
        score += 20; reasons.append("reputable domain")
    if html:
        if _has_c2pa(html):
            score += 25; reasons.append("content credentials (C2PA)")
        author, date = _extract_schema_meta(html)
        if author:
            score += 15; reasons.append("named author")
        if date:
            score += 10; reasons.append("timestamp present")
        ol = _outbound_link_score(html)
        if ol >= 0.2:
            bonus = int(10 * ol)
            score += bonus; reasons.append(f"citations/outbound links ({bonus}/10)")
        # corroboration using title/snippet
        phrase = item.get("title") or item.get("snippet") or ""
        if phrase and _corroborate_claim(phrase):
            score += 15; reasons.append("independent corroboration")
    # Cap to 100
    score = max(0, min(100, score))
    label = "High" if score >= 75 else ("Medium" if score >= 50 else "Low")
    return score, label, reasons

def assess_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        s, lab, why = reliability_score(it)
        enriched = dict(it)
        enriched["reliability_score"] = s
        enriched["reliability_label"] = lab
        enriched["reliability_reasons"] = why
        out.append(enriched)
    return out
