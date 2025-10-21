# backend/main_agent.py
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from backend.router_agent import route_query
from backend.retriever import retrieve_pico_papers
from backend.guidelines import fetch_guidelines_for_query
from backend.blog_news_retriever import web_search_duckduckgo
from backend.provenance_validator import assess_items
from backend.synthesizer import synthesize

def _enrich_and_summarize(route, items):
    # Reliability assessment (adds reliability_score/label/reasons)
    items_scored = assess_items(items)

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
    route = route_query(user_query)
    print("\n--- ROUTER OUTPUT ---")
    print(route)

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
        guides = fetch_guidelines_for_query(
            clean_query=route.clean_query,
            geography=route.geography,
            k_per_site=3,
        )
        print("\n--- GUIDELINES ---")
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

        items = (
            [{
                "source_type": "guideline",
                "title": g.title, "snippet": g.snippet, "year": g.year,
                "url": g.url, "org": g.org
            } for g in guides] +
            [{
                "source_type": "paper",
                "title": s.title, "snippet": s.snippet, "year": s.year,
                "url": s.url, "org": None, "study_type": s.study_type,
                "population": s.population
            } for s in support_papers] +
            [{
                "source_type": "web",
                "title": w.title, "snippet": w.snippet, "year": w.year,
                "url": w.url, "org": w.org
            } for w in web_hits]
        )

        return _enrich_and_summarize(route, items)

    else:
        print("\nRouter asked for clarification; skipping retrieval.")
        return {"summary_block": "Please clarify your question (age range, region/org, or outcome)."}

if __name__ == "__main__":
    # q = "Does kangaroo care reduce hospital stay in preterm infants?"
    q = "Compare infant vaccination schedules between WHO and CDC"
    run_pipeline(q)
