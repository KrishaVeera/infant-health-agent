# backend/validator.py
from __future__ import annotations
from typing import List, Dict, Any
from datetime import datetime
import re

BANNER = "⚠️ Research synthesis for information only — not medical advice."

PREPRINT_HINTS = ("medrxiv.org", "biorxiv.org", "arxiv.org", "ssrn.com", "researchsquare.com", "osf.io", "morressier")

def _citations_in_text(text: str) -> List[int]:
    return sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})

def _out_of_range_cites(used: List[int], max_idx: int) -> List[int]:
    return [i for i in used if i < 1 or i > max_idx]

def _is_preprint(url: str | None) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(h in u for h in PREPRINT_HINTS)

def validate(summary_block: str, items: List[Dict[str, Any]], time_horizon_years: int = 5) -> Dict[str, Any]:
    """
    Ensures:
      - bracket citations map to available sources
      - recency warnings (> time_horizon_years)
      - preprints flagged
      - banner present (idempotent)
    Returns: {"validated_text": str, "issues": [str]}
    """
    issues: List[str] = []

    # 1) citation coverage
    used = _citations_in_text(summary_block)
    max_idx = len(items)
    oob = _out_of_range_cites(used, max_idx)
    if oob:
        issues.append(f"Citation indexes out of range: {oob} (max is {max_idx}).")

    if not used and max_idx > 0:
        issues.append("No bracket citations [#] found in summary.")

    # 2) recency
    cur = datetime.utcnow().year
    old_years = sorted({it.get("year") for it in items if it.get("year") and cur - int(it["year"]) > time_horizon_years})
    if old_years:
        issues.append(f"Some sources older than {time_horizon_years} years: {old_years}")

    # 3) preprints
    if any(_is_preprint(it.get("url")) for it in items):
        issues.append("Includes preprint/abstract host(s); interpret cautiously.")

    # 4) ensure banner
    out_text = summary_block
    if BANNER not in out_text:
        out_text += f"\n\n{BANNER}"

    return {"validated_text": out_text, "issues": issues}
