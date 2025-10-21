# backend/guidelines.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional
import requests, re
from bs4 import BeautifulSoup
from datetime import datetime

UA = "infant-health-agent/0.1 (+https://example.com)"  # any UA string

@dataclass
class GuidelineItem:
    source_type: str
    title: str
    snippet: Optional[str]
    year: Optional[int]
    url: str
    org: Optional[str]

def _extract_year(text: str) -> Optional[int]:
    """Extract the most plausible year from text, or return None if none found."""
    if not text:
        return None
    years = re.findall(r"(20\d{2}|19\d{2})", text or "")
    if not years:
        return None
    # Keep only years in a realistic range
    valid = []
    current_year = datetime.utcnow().year
    for y in years:
        try:
            yi = int(y)
            if 1900 <= yi <= current_year:
                valid.append(yi)
        except Exception:
            continue
    if not valid:
        return None
    return max(valid)

def _ddg_site_search(site: str, query: str, k: int = 4) -> List[GuidelineItem]:
    """
    Light HTML search via DuckDuckGo (no API key).
    Filters results by domain. Returns top-k items.
    """
    q = f"site:{site} {query}"
    url = "https://duckduckgo.com/html/"
    headers = {"User-Agent": UA}
    params = {"q": q}
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items: List[GuidelineItem] = []
    for res in soup.select(".result__a"):
        title = res.get_text(" ", strip=True)
        href = res.get("href")
        if not href or site not in href:
            continue
        # snippet lives in a sibling div
        snippet_el = res.find_parent(class_="result").select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else None
        # Use safe concat (snippet may be None)
        year = _extract_year(f"{title} {(snippet or '')} {href}")
        org = "WHO" if "who.int" in href else ("CDC" if "cdc.gov" in href else None)

        items.append(
            GuidelineItem(
                source_type="guideline",
                title=title,
                snippet=snippet,
                year=year,
                url=href,
                org=org,
            )
        )
        if len(items) >= k:
            break
    return items

def fetch_guidelines_for_query(clean_query: str,
                               geography: Optional[str] = None,
                               k_per_site: int = 3) -> List[GuidelineItem]:
    """
    If geography is specified (e.g., "WHO" or "US/CDC"), search that site first.
    Otherwise, search WHO and CDC.
    """
    out: List[GuidelineItem] = []
    sites = []
    if geography and geography.upper().startswith("WHO"):
        sites = ["who.int"]
    elif geography and ("CDC" in geography.upper() or "US" in geography.upper()):
        sites = ["cdc.gov"]
    else:
        sites = ["who.int", "cdc.gov"]

    for s in sites:
        out.extend(_ddg_site_search(s, clean_query, k=k_per_site))
    return out

def to_dicts(items: List[GuidelineItem]) -> List[dict]:
    return [asdict(x) for x in items]

# Quick test:  python backend/guidelines.py
if __name__ == "__main__":
    hits = fetch_guidelines_for_query("infant immunization schedule", geography=None)
    for h in hits:
        print(f"- {h.org} {h.year or ''} | {h.title} -> {h.url}")
