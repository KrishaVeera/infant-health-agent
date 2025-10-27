# backend/main_agent.py
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from backend.router_agent import route_query
from backend.retriever import retrieve_pico_papers
from backend.guidelines import fetch_guidelines_for_query
from backend.blog_news_retriever import web_search_duckduckgo
from backend.provenance_validator import assess_items
from backend.synthesizer import synthesize

def ask_for_clarification_if_needed(route, user_query: str):
    """
    If router provided clarify prompts, ask user interactively and re-route.
    Returns a potentially-updated route (or the original if no clarify needed).
    """
    if route.clarify:
        print("\nThe router needs clarification before continuing:")
        for c in route.clarify:
            print(" -", c)
        user_input = input("\nPlease provide clarification (or press Enter to skip): ").strip()
        if not user_input:
            print("No clarification provided; continuing with best-effort search.")
            return route
        # merge user input into original query and re-route
        combined = f"{user_query} {user_input}"
        return route_query(combined)
    return route

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
    print("\n--- SYNTHESIZER OUTPUT ---\n")
    print(result["summary_block"])
    return result

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
        # first try with provided geography (if any)
        guides = fetch_guidelines_for_query(
            clean_query=route.clean_query,
            geography=route.geography,
            k_per_site=3,
        )
        print("\n--- GUIDELINES (initial search) ---")
        for g in guides:
            print(f"- {g.org} {g.year or ''} | {g.title} -> {g.url}")

        # If empty, broaden the search (fallback) and increase k
        fallback_note = None
        if not guides:
            print("\nNo WHO/CDC guidelines found for the exact query. Performing a broader guideline search...")
            guides = fetch_guidelines_for_query(
                clean_query=route.clean_query,
                geography=None,
                k_per_site=6,
            )
            fallback_note = (
                "NOTE: No direct WHO/CDC match found for the exact query — "
                "results below come from a broader search and may be less specific. "
                "Try adding age/region (e.g., '0-6 months' or 'Canada') to improve precision."
            )

        print("\n--- GUIDELINES (final) ---")
        for g in guides:
            print(f"- {g.org} {g.year or ''} | {g.title} -> {g.url}")

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

        # Blogs/news (context + corroboration)
        web_hits = web_search_duckduckgo(route.clean_query, k=3)
        print("\n--- WEB (blogs/news) ---")
        for w in web_hits:
            print(f"- {w.year or ''} | {w.org} | {w.title} -> {w.url}")

        # Build items list; if guides empty, add explanatory guideline note item
        items = []
        if guides:
            items.extend([{
                "source_type": "guideline",
                "title": g.title, "snippet": g.snippet, "year": g.year,
                "url": g.url, "org": g.org
            } for g in guides])
        else:
            # explanatory placeholder so synthesizer always has at least one guideline-like item
            note = fallback_note or (
                "No WHO/CDC guideline matched the exact query. "
                "A broader search was performed; results may be less specific."
            )
            items.append({
                "source_type": "guideline",
                "title": "No direct guideline found for exact query",
                "snippet": note,
                "year": None,
                "url": "",
                "org": "NOTE"
            })

        items.extend([{
            "source_type": "paper",
            "title": s.title, "snippet": s.snippet, "year": s.year,
            "url": s.url, "org": None, "study_type": s.study_type,
            "population": s.population
        } for s in support_papers])

        items.extend([{
            "source_type": "web",
            "title": w.title, "snippet": w.snippet, "year": w.year,
            "url": w.url, "org": w.org
        } for w in web_hits])

        return _enrich_and_summarize(route, items)

    else:
        print("\nRouter asked for clarification; skipping retrieval.")
        return {"summary_block": "Please clarify your question (age range, region/org, or outcome)."}

if __name__ == "__main__":
    # Try guideline compare (interactive if router asks)
    import sys
    q = " ".join(sys.argv[1:]) or "Compare infant vaccination schedules between WHO and CDC"
    run_pipeline(q)
