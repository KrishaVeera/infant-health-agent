# backend/main_agent.py
import os, sys, re
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from backend.router_agent import route_query
from backend.retriever import retrieve_pico_papers
from backend.guidelines import fetch_guidelines_for_query
from backend.blog_news_retriever import web_search_duckduckgo
from backend.provenance_validator import assess_items
from backend.synthesizer import synthesize
from backend.validator import validate

# ---------------- helpers ----------------

def ask_for_clarification_if_needed(route, user_query: str):
    """
    If router provided clarify prompts, ask user interactively and re-route.
    Returns a potentially-updated route (or the original if no clarify needed).
    """
    if getattr(route, "clarify", None):
        print("\nThe router needs clarification before continuing:")
        for c in route.clarify:
            print(" -", c)
        user_input = input("\nPlease provide clarification (or press Enter to skip): ").strip()
        if not user_input:
            print("No clarification provided; continuing with best-effort search.")
            return route
        combined = f"{user_query} {user_input}"
        return route_query(combined)
    return route

def _strip_compare_words(q: str) -> str:
    # remove leading “compare/versus/compare … between … and …” to broaden web search
    q = re.sub(r"\b(compare|versus|vs\.?)\b", "", q, flags=re.I).strip()
    q = re.sub(r"\bbetween\b", "", q, flags=re.I).strip()
    q = re.sub(r"\s{2,}", " ", q)
    return q

def _broaden_query(base: str, route) -> list[str]:
    """
    Produce a few progressively-broader queries for a 2nd-chance web search.
    Ordered from most-specific to broadest.
    """
    stripped = _strip_compare_words(base)
    terms = ["infant", "vaccination", "schedule"]
    alt1 = f"{stripped} " + " ".join(terms)
    alt2 = " ".join(terms + ["WHO", "CDC"])
    alt3 = "infant immunization schedule WHO CDC"
    return [alt1, alt2, alt3]

def _enrich_and_summarize(route, items):
    # Reliability assessment (adds reliability_score/label/reasons)
    items_scored = assess_items(items)

    # diagnostic: print reliability reasons for each item (console)
    print("\n--- SOURCE RELIABILITY (diagnostic) ---")
    for it in items_scored:
        print(f"- [{it.get('reliability_label')}] {it.get('title')}")
        reasons = it.get("reliability_reasons") or []
        if reasons:
            for r in reasons:
                print("   -", r)

    # Synthesize
    result = synthesize(
        task_type=route.task_type,
        route_meta={
            "population": route.population,
            "outcomes": route.outcomes,
            "geography": route.geography,
            "units_hint": route.units_hint,
        },
        items=items_scored,
    )

    # Validate synthesized text
    val = validate(result["summary_block"], items_scored, time_horizon_years=route.time_horizon_years)

    print("\n--- VALIDATED OUTPUT ---\n")
    print(val["validated_text"])
    if val["issues"]:
        print("\n(validator notes)")
        for i in val["issues"]:
            print("-", i)
    return val

# ---------------- main pipeline ----------------

def run_pipeline(user_query: str):
    # initial route
    route = route_query(user_query)
    print("\n--- ROUTER OUTPUT ---")
    print(route)

    # interactive clarification loop:
    route = ask_for_clarification_if_needed(route, user_query)

    if route.task_type == "PICO_EVIDENCE":
        papers = retrieve_pico_papers(
            clean_query=route.clean_query,
            time_horizon_years=route.time_horizon_years,
            population=route.population,
            outcomes=route.outcomes,
            k=6,
        )
        print("\n--- PAPERS ---")
        for d in papers:
            print(f"- {d.year} | {d.study_type or 'Study'} | {d.title} -> {d.url}")

        items = [{
            "source_type": "paper",
            "title": d.title, "snippet": d.snippet, "year": d.year,
            "url": d.url, "org": None, "study_type": d.study_type,
            "population": d.population
        } for d in papers]

        return _enrich_and_summarize(route, items)

    elif route.task_type == "GUIDELINE_COMPARE":
        # 1) Guidelines (never empty)
        guides = fetch_guidelines_for_query(
            clean_query=route.clean_query,
            geography=route.geography,
            k_per_site=3,
        )
        print("\n--- GUIDELINES (initial search) ---")
        for g in guides:
            print(f"- {g.org} {g.year or ''} | {g.title} -> {g.url}")

        fallback_note = None
        if not guides:
            print("\nNo WHO/CDC guidelines found for the exact query. Performing a broader guideline search...")
            guides = fetch_guidelines_for_query(
                clean_query=route.clean_query,
                geography=None,
                k_per_site=6,
            )
            fallback_note = (
                "No direct WHO/CDC guideline matched the exact query — "
                "broader search used; add age/region keywords to improve precision."
            )

        print("\n--- GUIDELINES (final) ---")
        for g in guides:
            print(f"- {g.org} {g.year or ''} | {g.title} -> {g.url}")

        # 2) Support papers
        support_papers = []
        if route.need_supporting_evidence:
            support_papers = retrieve_pico_papers(
                clean_query=route.clean_query,
                time_horizon_years=route.time_horizon_years,
                population=route.population,
                outcomes=route.outcomes,
                k=2,
            )
            print("\n--- SUPPORTING PAPERS ---")
            for s in support_papers:
                print(f"- {s.year} | {s.study_type or 'Study'} | {s.title} -> {s.url}")

        # 3) Web (blogs/news) with retry + note (never empty)
        web_hits = web_search_duckduckgo(route.clean_query, k=3)
        web_note = None
        if not web_hits:
            print("\nNo relevant blogs/news for the exact query. Trying broader web queries...")
            for alt in _broaden_query(route.clean_query, route):
                web_hits = web_search_duckduckgo(alt, k=5)
                if web_hits:
                    web_note = f"Broadened web query used: “{alt}”."
                    break

        print("\n--- WEB (blogs/news) ---")
        if web_hits:
            for w in web_hits:
                print(f"- {w.year or ''} | {w.org} | {w.title} -> {w.url}")
        else:
            print("- (no hits after broadening)")

        # 4) Build items (insert NOTE rows if needed so synthesizer always has context)
        items = []
        if guides:
            items.extend([{
                "source_type": "guideline",
                "title": g.title, "snippet": g.snippet, "year": g.year,
                "url": g.url, "org": g.org
            } for g in guides])
        else:
            items.append({
                "source_type": "guideline",
                "title": "No direct guideline found for exact query",
                "snippet": fallback_note or "Broader search used.",
                "year": None, "url": "", "org": "NOTE"
            })

        items.extend([{
            "source_type": "paper",
            "title": s.title, "snippet": s.snippet, "year": s.year,
            "url": s.url, "org": None, "study_type": s.study_type,
            "population": s.population
        } for s in support_papers])

        if web_hits:
            items.extend([{
                "source_type": "web",
                "title": w.title, "snippet": w.snippet, "year": w.year,
                "url": w.url, "org": w.org
            } for w in web_hits])
        else:
            items.append({
                "source_type": "web",
                "title": "No blogs/news matched the exact query",
                "snippet": web_note or "Tried broader queries; consider rephrasing.",
                "year": None, "url": "", "org": "NOTE"
            })

        return _enrich_and_summarize(route, items)

    else:
        print("\nRouter asked for clarification; skipping retrieval.")
        return {"summary_block": "Please clarify your question (age range, region/org, or outcome)."}

if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Compare infant vaccination schedules between WHO and CDC"
    run_pipeline(q)
