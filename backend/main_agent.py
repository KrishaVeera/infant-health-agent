# backend/main_agent.py
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from backend.router_agent import route_query
from backend.retriever import retrieve_pico_papers, to_dicts as papers_to_dicts
from backend.guidelines import fetch_guidelines_for_query, to_dicts as guides_to_dicts

def run_pipeline(user_query: str):
    route = route_query(user_query)
    print("\n--- ROUTER OUTPUT ---")
    print(route)

    docs = []
    if route.task_type == "PICO_EVIDENCE":
        docs = retrieve_pico_papers(
            clean_query=route.clean_query,
            time_horizon_years=route.time_horizon_years,
            population=route.population,
            outcomes=route.outcomes,
            k=6
        )
        print("\n--- PAPERS ---")
        for d in docs:
            print(f"- {d.year} | {d.study_type or 'Study'} | {d.title} -> {d.url}")
        return papers_to_dicts(docs)

    elif route.task_type == "GUIDELINE_COMPARE":
        guides = fetch_guidelines_for_query(
            clean_query=route.clean_query,
            geography=route.geography,
            k_per_site=3
        )
        print("\n--- GUIDELINES ---")
        for g in guides:
            print(f"- {g.org} {g.year or ''} | {g.title} -> {g.url}")

        # Optional: attach 2–3 supporting papers if requested
        if route.need_supporting_evidence:
            support = retrieve_pico_papers(
                clean_query=route.clean_query,
                time_horizon_years=route.time_horizon_years,
                population=route.population,
                outcomes=route.outcomes,
                k=3
            )
            print("\n--- SUPPORTING PAPERS ---")
            for s in support:
                print(f"- {s.year} | {s.study_type or 'Study'} | {s.title} -> {s.url}")
        return guides_to_dicts(guides)

    else:
        print("\nRouter asked for clarification; skipping retrieval.")
        return []

if __name__ == "__main__":
    # Try a guideline compare:
    q = "Compare infant vaccination schedules between WHO and CDC"
    run_pipeline(q)
