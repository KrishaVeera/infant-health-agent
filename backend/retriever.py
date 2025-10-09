# backend/retriever.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
import requests, time, re
from datetime import datetime

CROSSREF_URL = "https://api.crossref.org/works"
UA = "infant-health-agent/0.1 (mailto:veerakrisha123@gmail.com)"  

@dataclass
class DocItem:
    source_type: str       
    title: str
    snippet: Optional[str]
    year: Optional[int]
    url: Optional[str]
    org: Optional[str] = None
    doi: Optional[str] = None
    study_type: Optional[str] = None   # "RCT" | "meta-analysis" | ...
    population: Optional[str] = None
    is_preprint: bool = False

def _year_filter(from_year: int) -> Dict[str, Any]:
    return {
        "filter": f"from-pub-date:{from_year}-01-01",
    }

def _study_type_from(title: str, abstract: str) -> Optional[str]:
    text = f"{title} {abstract}".lower()
    if any(k in text for k in ["randomized", "randomised", "randomized controlled", "rct"]):
        return "RCT"
    if "meta-analysis" in text or "systematic review" in text:
        return "Meta-analysis"
    if any(k in text for k in ["cohort", "prospective", "retrospective"]):
        return "Cohort"
    if "case-control" in text:
        return "Case-control"
    return None

def _is_preprint(item: dict) -> bool:
    host = (item.get("URL") or "").lower()
    return any(h in host for h in ["medrxiv", "biorxiv", "arxiv"])

def _first_str(x) -> str:
    if isinstance(x, list) and x:
        return x[0]
    return x or ""

def _get_year(item: dict) -> Optional[int]:
    for key in ["published-print", "published-online", "issued", "created"]:
        d = item.get(key, {}).get("date-parts", [])
        if d and len(d[0]) >= 1:
            try:
                return int(d[0][0])
            except Exception:
                pass
    return None

def _normalize_title(t: str) -> str:
    t = t or ""
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def crossref_search_papers(query: str, year_from: int, k: int = 6) -> List[DocItem]:
    """Query Crossref for papers and return top-k normalized items"""
    params = {
        "query": query,
        "rows": max(10, k * 2),   # fetch a few extra for de-dupe
        **_year_filter(year_from),
        "select": "title,abstract,DOI,URL,issued,created,publisher,container-title,type",
        "sort": "score",
        "order": "desc",
    }
    headers = {"User-Agent": UA}

    r = requests.get(CROSSREF_URL, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    items = r.json().get("message", {}).get("items", [])

    seen_titles = set()
    seen_dois = set()
    out: List[DocItem] = []

    for it in items:
        title = _first_str(it.get("title"))
        if not title:
            continue
        doi = it.get("DOI")
        year = _get_year(it)
        url = it.get("URL") or (f"https://doi.org/{doi}" if doi else None)
        abstract = it.get("abstract") or ""
        study_type = _study_type_from(title, abstract)
        preprint = _is_preprint(it)

        # de-dupe
        nt = _normalize_title(title)
        if doi and doi in seen_dois:
            continue
        if nt in seen_titles:
            continue

        out.append(
            DocItem(
                source_type="paper",
                title=title,
                snippet=re.sub("<.*?>", "", abstract)[:350] or None,
                year=year,
                url=url,
                org=None,
                doi=doi,
                study_type=study_type,
                population=None,
                is_preprint=preprint,
            )
        )
        if doi: seen_dois.add(doi)
        seen_titles.add(nt)
        if len(out) >= k:
            break

    return out

def build_query(clean_query: str,
                population: Optional[str] = None,
                outcomes: Optional[List[str]] = None) -> str:
    # Simple booster: add population/outcomes terms if present
    parts = [clean_query]
    if population:
        parts.append(population)
    if outcomes:
        parts.extend(outcomes[:2])
    # Prefer trials & syntheses
    parts.extend(["randomized", "trial", "meta-analysis"])
    return " ".join(parts)

def retrieve_pico_papers(clean_query: str,
                         time_horizon_years: int = 5,
                         population: Optional[str] = None,
                         outcomes: Optional[List[str]] = None,
                         k: int = 6) -> List[DocItem]:
    year_from = datetime.utcnow().year - max(0, int(time_horizon_years))
    q = build_query(clean_query, population, outcomes)
    results = crossref_search_papers(q, year_from, k=k)

    # If too few results, relax by widening years
    if len(results) < max(3, k // 2):
        results = crossref_search_papers(q, year_from - 5, k=k)

    return results

def to_dicts(items: List[DocItem]) -> List[dict]:
    return [asdict(x) for x in items]

# ---- quick CLI demo ----
# Usage:
#   python backend/retriever.py "Does kangaroo care reduce hospital stay in preterm infants?"
if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) or "Does kangaroo care reduce hospital stay in preterm infants?"
    docs = retrieve_pico_papers(clean_query=query, time_horizon_years=5, population="preterm", outcomes=["length of stay"], k=6)
    for d in docs:
        print(f"- {d.year} | {d.study_type or 'Study'} | {d.title}  -> {d.url}")
