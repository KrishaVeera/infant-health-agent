# backend/blog_news_retriever.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests, re

UA = "infant-health-agent/0.1 (+https://example.com)"  # any UA string
DDG_HTML = "https://duckduckgo.com/html/"

REPUTABLE_HINTS = (
    ".gov", ".edu",
    "who.int", "cdc.gov", "unicef.org", "nih.gov", "bmj.com",
    "nature.com", "sciencedirect.com", "jamanetwork.com",
    "thelancet.com", "nejm.org", "springer.com", "wiley.com",
    "tandfonline.com", "oup.com", "jamanetwork.com",
    "reuters.com", "apnews.com", "nytimes.com", "bbc.com", "theguardian.com",
)

@dataclass
class WebItem:
    source_type: str  # "web"
    title: str
    snippet: Optional[str]
    url: str
    year: Optional[int]
    org: Optional[str]  # domain only

def _extract_year(text: str) -> Optional[int]:
    m = re.findall(r"(20\d{2}|19\d{2})", text or "")
    if not m: return None
    return max(int(y) for y in m)

def _domain(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ""

def _is_reputable_domain(domain: str) -> bool:
    return any(h in domain for h in REPUTABLE_HINTS)

def web_search_duckduckgo(query: str, k: int = 5) -> List[WebItem]:
    params = {"q": query}
    headers = {"User-Agent": UA}
    r = requests.get(DDG_HTML, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items: List[WebItem] = []
    for a in soup.select(".result__a"):
        title = a.get_text(" ", strip=True)
        href = a.get("href")
        if not href:
            continue
        card = a.find_parent(class_="result")
        snippet_el = card.select_one(".result__snippet") if card else None
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else None
        yr = _extract_year(" ".join([title, snippet or "", href]))
        org = _domain(href)

        items.append(WebItem(
            source_type="web",
            title=title,
            snippet=snippet,
            url=href,
            year=yr,
            org=org,
        ))
        if len(items) >= k:
            break

    # Stable ordering trick: reputable first, then others
    items.sort(key=lambda x: (not _is_reputable_domain(x.org or ""), x.title.lower()))
    return items

def to_dicts(items: List[WebItem]) -> List[dict]:
    return [asdict(i) for i in items]

if __name__ == "__main__":
    hits = web_search_duckduckgo("infant vaccination WHO CDC")
    for h in hits:
        print("-", h.year or "", "|", h.org, "|", h.title, "->", h.url)
