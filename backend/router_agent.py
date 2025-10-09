# backend/router_agent.py
from dataclasses import dataclass, field
from typing import Literal, List, Optional
import json, re, sys
from openai import OpenAI

MODEL = "gpt-4.1-mini"   # small, inexpensive model is fine here

TaskType = Literal["PICO_EVIDENCE", "GUIDELINE_COMPARE", "CLARIFY"]

@dataclass
class RouterOutput:
    task_type: TaskType
    clean_query: str
    time_horizon_years: int = 5
    # ---- new helpful fields for downstream agents ----
    need_supporting_evidence: bool = False     # default True for guideline compare
    population: Optional[str] = None           # e.g., "preterm", "term", "0–6m"
    geography: Optional[str] = None            # e.g., "WHO", "US/CDC", "Canada/CPS"
    outcomes: List[str] = field(default_factory=list)  # e.g., ["diarrhea","length of stay"]
    units_hint: Optional[str] = None           # e.g., "IU/day"
    confidence: float = 0.0                    # router’s confidence (0–1)
    clarify: List[str] = field(default_factory=list)   # follow-up questions if unclear

SYSTEM = """You are a routing microservice.
Classify the user's query into exactly one of:
- PICO_EVIDENCE: 'does X affect Y', effectiveness, outcomes, trials, RCTs, meta-analyses.
- GUIDELINE_COMPARE: compare recommendations/policies/guidelines across orgs (WHO/CDC/etc).
- CLARIFY: the query is too vague or missing critical info.

Also extract minimal metadata to help downstream agents:
- population (e.g., "preterm", "term", "0–6m", "6–12m", or null)
- geography (e.g., "WHO", "US/CDC", "Canada/CPS", or null)
- outcomes (list of 1–3 short keywords like "diarrhea", "length of stay", "25OHD")
- units_hint (e.g., "IU/day" when dosage is mentioned, else null)
- confidence (0..1)
- clarify (0–2 short questions when info is missing)

Rules:
- If task_type is GUIDELINE_COMPARE, set need_supporting_evidence=true (we will attach 2–3 key studies when relevant).
- If essential fields for guideline compare are missing (e.g., age window or geography), set task_type=CLARIFY and include 1–2 clarify questions.
- Keep JSON compact. Do NOT add commentary or code fences.
Return ONLY JSON like:
{"task_type":"PICO_EVIDENCE","clean_query":"...","time_horizon_years":5,"need_supporting_evidence":false,"population":null,"geography":null,"outcomes":["diarrhea"],"units_hint":null,"confidence":0.8,"clarify":[]}
"""

# --------- tiny regex+heuristic fallback if the LLM ever fails ----------
def _rule_fallback(user_query: str) -> RouterOutput:
    q = user_query.strip()
    ql = q.lower()
    # naive guesses
    is_guideline = any(k in ql for k in ["guideline", "recommendation", "policy", "schedule", "who", "cdc"])
    is_pico = any(k in ql for k in ["does", "effect", "impact", "reduce", "increase", "trial", "rct", "meta-analysis"])
    task: TaskType = "CLARIFY"
    if is_guideline:
        task = "GUIDELINE_COMPARE"
    elif is_pico:
        task = "PICO_EVIDENCE"

    need_support = (task == "GUIDELINE_COMPARE")
    population = None
    if any(k in ql for k in ["preterm", "pre-term"]): population = "preterm"
    elif any(k in ql for k in ["term infant", "term baby"]): population = "term"
    elif any(k in ql for k in ["0-6", "0–6", "0 to 6", "0 – 6"]): population = "0–6m"
    elif any(k in ql for k in ["6-12", "6–12", "6 to 12", "6 – 12"]): population = "6–12m"

    geography = None
    if "who" in ql: geography = "WHO"
    elif "cdc" in ql or "united states" in ql or "us" in ql: geography = "US/CDC"
    elif "canada" in ql or "cps" in ql: geography = "Canada/CPS"

    units_hint = "IU/day" if "iu" in ql or "vitamin d" in ql else None

    outcomes: List[str] = []
    if any(k in ql for k in ["diarrhea", "gastroenteritis"]): outcomes.append("diarrhea")
    if any(k in ql for k in ["length of stay", "los"]): outcomes.append("length of stay")
    if any(k in ql for k in ["25ohd", "25-hydroxy", "25-hydroxyvitamin d"]): outcomes.append("25OHD")

    clarify: List[str] = []
    if task == "GUIDELINE_COMPARE" and geography is None:
        clarify.append("Which region/org should I compare (WHO, CDC, Canada/CPS)?")
    if task == "GUIDELINE_COMPARE" and population is None:
        clarify.append("Which age or population (e.g., 0–6 months, preterm, term)?")

    return RouterOutput(
        task_type=task,
        clean_query=q,
        time_horizon_years=5,
        need_supporting_evidence=need_support,
        population=population,
        geography=geography,
        outcomes=outcomes,
        units_hint=units_hint,
        confidence=0.4 if task == "CLARIFY" else 0.7,
        clarify=clarify
    )

def route_query(user_query: str) -> RouterOutput:
    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[
                {"role":"system", "content": SYSTEM},
                {"role":"user", "content": user_query}
            ]
        )
        text = resp.choices[0].message.content.strip()
        # extract first {...} in case model adds anything
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0) if match else text)

        # defaults & safety
        task = data.get("task_type", "CLARIFY")
        need_support = bool(data.get("need_supporting_evidence", task == "GUIDELINE_COMPARE"))

        return RouterOutput(
            task_type=task,  # type: ignore[arg-type]
            clean_query=data.get("clean_query", user_query.strip()),
            time_horizon_years=int(data.get("time_horizon_years", 5)),
            need_supporting_evidence=need_support,
            population=data.get("population"),
            geography=data.get("geography"),
            outcomes=list(data.get("outcomes", []))[:3],
            units_hint=data.get("units_hint"),
            confidence=float(data.get("confidence", 0.0)),
            clarify=list(data.get("clarify", []))[:2],
        )
    except Exception:
        # Safe fallback
        return _rule_fallback(user_query)

# --- quick CLI test: `python backend/router_agent.py "your question"`
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Compare vitamin D supplementation guidelines for term infants between WHO and CDC"
    out = route_query(q)
    print(out)
