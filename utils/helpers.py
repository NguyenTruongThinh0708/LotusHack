import json
import re
from typing import Any, Dict, Optional

_DISTRICT_PATTERNS = [
    r"\bquận\s*(\d{1,2})\b",
    r"\bq\.?\s*(\d{1,2})\b",
    r"\bdistrict\s*(\d{1,2})\b",
]

def extract_district(user_text: str) -> Optional[str]:
    t = (user_text or "").lower()
    for pat in _DISTRICT_PATTERNS:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            return f"quận {m.group(1)}"
    return None

def safe_json_loads(s: str, default: Any):
    try:
        return json.loads(s)
    except Exception:
        return default

def clamp_score_0_5(x: Any) -> float:
    try:
        v = float(x)
    except Exception:
        v = 0.0
    return max(0.0, min(5.0, v))

def normalize_ai_scores(payload: Dict[str, Any]) -> Dict[str, float]:
    scores = payload.get("scores") or {}
    return {
        "clean": clamp_score_0_5(scores.get("clean")),
        "safe": clamp_score_0_5(scores.get("safe")),
        "support": clamp_score_0_5(scores.get("support")),
        "speed": clamp_score_0_5(scores.get("speed")),
    }
