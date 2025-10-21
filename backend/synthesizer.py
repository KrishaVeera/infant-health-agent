# backend/synthesizer.py
from __future__ import annotations
from typing import List, Dict, Any, Literal
from openai import OpenAI
import textwrap, json

MODEL = "gpt-4.1-mini"
TaskType = Literal["PICO_EVIDENCE", "GUIDELINE_COMPARE", "CLARIFY"]

SYSTEM = """You are an evidence synthesizer for infant & child health.
Write concise, neutral summaries with citations.
Never give medical advice; include this banner verbatim at the end:
"⚠️ Research synthesis for information only — not medical advice."
Rules:
- 130 words max for the summary.
- Use only the provided items (papers/guidelines/web).
- Cite using bracket numbers [1], [2] that match the Sources list.
- If evidence conflicts, say so explicitly.
- Prefer recent/high-quality sources (RCTs, meta-analyses; official orgs)."""

def _source_line(i: int, it: Dict[str, Any]) -> str:
    year = it.get("year") or ""
    title = it.get("title") or "Untitled"
    org = it.get("org") or ""
    url = it.get("url") or it.get("doi") or ""
    rel = it.get("reliability_label")
    rel_tag = f" [{rel}]" if rel else ""
    if org:
        return f"[{i}] {org} {year}. {title}. {url}{rel_tag}"
    return f"[{i}] {year}. {title}. {url}{rel_tag}"

def _format_sources(items: List[Dict[str, Any]]) -> str:
    return "\n".join(_source_line(i, it) for i, it in enumerate(items, start=1))

def _table_prompt(task_type: TaskType) -> str:
    if task_type == "GUIDELINE_COMPARE":
        return textwrap.dedent("""
        Return ALSO a markdown table with columns:
        | Org | Topic | Recommendation/Notes | Last Updated/Year | Link |
        Fill only with guideline/web items for policy; put study items (papers) only in Sources.
        """)
    else:
        return textwrap.dedent("""
        Return ALSO a markdown table with columns:
        | Study | Type | N/Population | Main Finding | Year | Link |
        Fill only with paper items.
        """)

def synthesize(task_type: TaskType,
               route_meta: Dict[str, Any],
               items: List[Dict[str, Any]]) -> Dict[str, str]:
    # Keep numbering stable
    payload = []
    for it in items:
        payload.append({
            "source_type": it.get("source_type"),
            "title": it.get("title"),
            "year": it.get("year"),
            "org": it.get("org"),
            "url": it.get("url"),
            "study_type": it.get("study_type"),
            "population": it.get("population"),
            "snippet": (it.get("snippet") or "")[:400],
            "reliability_label": it.get("reliability_label"),
        })

    meta_bits = []
    if route_meta.get("population"): meta_bits.append(f"Population: {route_meta['population']}")
    if route_meta.get("outcomes"): meta_bits.append(f"Outcomes: {', '.join(route_meta['outcomes'])}")
    if route_meta.get("geography"): meta_bits.append(f"Geography: {route_meta['geography']}")
    if route_meta.get("units_hint"): meta_bits.append(f"Units: {route_meta['units_hint']}")
    meta_line = " | ".join(meta_bits) if meta_bits else "Context: general infant/child health"

    sources_text = _format_sources(payload)
    table_instr = _table_prompt(task_type)

    user_prompt = f"""
    Task type: {task_type}
    {meta_line}

    Provided items (numbered for citation order):
    {json.dumps(payload, ensure_ascii=False, indent=2)}

    Write a 3–5 sentence summary (<=130 words), neutral tone, cite with [#].
    {table_instr}

    End with the fixed banner: ⚠️ Research synthesis for information only — not medical advice.

    After summary and table, output a section:
    Sources:
    {sources_text}
    """

    client = OpenAI()
    resp = client.chat.completions.create(
        model=MODEL, temperature=0.2,
        messages=[{"role":"system","content":SYSTEM},{"role":"user","content":user_prompt}]
    )
    return {"summary_block": resp.choices[0].message.content.strip()}
