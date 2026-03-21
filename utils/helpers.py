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

def clamp_score(x: Any, lo: float = -2.0, hi: float = 2.0) -> float:
    """Clamp value vào khoảng [lo, hi]. Mặc định scale -2..+2."""
    try:
        v = float(x)
    except Exception:
        v = 0.0
    return max(lo, min(hi, v))

def normalize_ai_scores(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Chuẩn hoá scores từ LLM response về đúng scale -2..+2."""
    scores = payload.get("scores") or {}
    return {
        "clean": clamp_score(scores.get("clean", 0)),
        "safe": clamp_score(scores.get("safe", 0)),
        "support": clamp_score(scores.get("support", 0)),
        "speed": clamp_score(scores.get("speed", 0)),
        "price": clamp_score(scores.get("price", 0)),
        "is_closed": bool(scores.get("is_closed", False)),
    }
