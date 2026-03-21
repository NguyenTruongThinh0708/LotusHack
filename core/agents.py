"""
Multi-agent system for SafeWash AI.

Agents:
  1. RouterAgent   — Phân loại intent của user, quyết định gọi tool nào
  2. AnalystAgent  — Phân tích data, tính toán trust index
  3. AdvisorAgent  — Tổng hợp và tư vấn cho user bằng tiếng Việt

All prompts are centralized in core/prompts.py
"""
import json
import os
from openai import OpenAI
from core.evaluator import SafeWashEvaluator
from core.prompts import ROUTER_PROMPT, ADVISOR_PROMPT, TIPS_PROMPT
from utils.helpers import safe_json_loads

_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


# ---------------------------------------------------------------------------
# ROUTER AGENT — lightweight, classifies user intent
# ---------------------------------------------------------------------------
def route(user_message: str) -> dict:
    """Classify user intent + extract entities."""
    resp = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return safe_json_loads(resp.choices[0].message.content, {"intent": "general"})


# ---------------------------------------------------------------------------
# ANALYST AGENT — enriches raw shop data with trust scores
# ---------------------------------------------------------------------------
MIN_TRUST_THRESHOLD = 3.0

def analyze_shops(shops_json: str, sort_order: str = "best", apply_threshold: bool = True) -> list[dict]:
    """Parse shop list, compute trust index & risk label for each."""
    parsed = safe_json_loads(shops_json, [])
    # Handle single dict (from get_audit_evidence) or non-list
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    shops = [s for s in parsed if isinstance(s, dict)]
    for shop in shops:
        m = shop.get("metrics", {})
        shop["_trust"] = SafeWashEvaluator.calculate_trust_index(m)
        shop["_risk"] = SafeWashEvaluator.risk_label(m)
    # Sort: worst-first or best-first, closed always last
    if sort_order == "worst":
        shops.sort(key=lambda s: (s["_risk"] == "CLOSED", s["_trust"]))
    else:
        shops.sort(key=lambda s: (s["_risk"] == "CLOSED", -s["_trust"]))
    # Threshold filter for recommendations (skip for worst/specific queries)
    if apply_threshold and sort_order != "worst":
        shops = [s for s in shops if s["_trust"] >= MIN_TRUST_THRESHOLD]
    return shops


# ---------------------------------------------------------------------------
# ADVISOR AGENT — generates final Vietnamese response
# ---------------------------------------------------------------------------
def advise(user_message: str, analyzed_data: list[dict], intent_info: dict) -> dict:
    """Generate final user-facing response."""
    # Trim data to reduce token cost — keep top 8 shops max for context
    trimmed = []
    for s in analyzed_data[:8]:
        trimmed.append({
            "name": s.get("name"),
            "phone": s.get("phone"),
            "address": s.get("additional_info", {}).get("address", ""),
            "metrics": s.get("metrics"),
            "trust_index": s.get("_trust"),
            "risk_label": s.get("_risk"),
            "working_hours": s.get("working_hours"),
            "busyness_sample": (s.get("busyness") or [])[:6],
            "live_busyness": s.get("live_busyness"),
            "top_reviews": [
                {"text": r.get("text", ""), "rating": r.get("rating")}
                for r in (s.get("top_reviews") or [])[:3]
            ],
            "website": s.get("website"),
            "services": s.get("additional_info", {}).get("services"),
        })

    user_payload = json.dumps({
        "user_question": user_message,
        "intent": intent_info,
        "shops": trimmed,
    }, ensure_ascii=False)

    resp = _get_client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": ADVISOR_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        response_format={"type": "json_object"},
    )
    return safe_json_loads(resp.choices[0].message.content, {"summary": "Xin lỗi, có lỗi xảy ra."})


# ---------------------------------------------------------------------------
# GENERAL TIPS (no data needed, cheap model)
# ---------------------------------------------------------------------------
def general_tips(user_message: str) -> dict:
    resp = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": TIPS_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    return safe_json_loads(resp.choices[0].message.content, {"summary": "Xin lỗi, tôi chưa trả lời được câu này."})
