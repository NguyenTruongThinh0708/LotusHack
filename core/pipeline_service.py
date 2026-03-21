import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.agents import advise, analyze_shops, general_tips, route
from core.evaluator import SafeWashEvaluator
from utils.helpers import normalize_ai_scores


@dataclass
class PipelineResult:
    display_text: str
    shops: List[Dict[str, Any]]
    intent_info: Dict[str, Any]
    logs: List[str] = field(default_factory=list)


class SafeWashPipeline:
    def __init__(self, server_script: Path | None = None):
        if server_script is None:
            server_script = Path(__file__).parent.parent / "server" / "mcp_server.py"
        self.server_params = StdioServerParameters(
            command=sys.executable,
            args=[str(server_script)],
            env=os.environ.copy(),
        )

    async def fetch_data_from_mcp(self, intent_info: Dict[str, Any], logs: List[str]) -> str:
        """Call the appropriate MCP tool based on router's intent classification."""
        intent = intent_info.get("intent", "general")
        location = intent_info.get("location")
        shop_name = intent_info.get("shop_name")

        try:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    logs.append("✅ MCP connected")

                    if intent == "inspect" and shop_name:
                        logs.append(f"🔍 Inspecting: {shop_name}")
                        resp = await session.call_tool("get_audit_evidence", {"shop_name": shop_name})
                    elif intent == "compare" and shop_name:
                        logs.append(f"⚖️ Comparing: {shop_name}")
                        resp = await session.call_tool("compare_shops", {"shop_names": shop_name})
                    elif intent == "busyness" and shop_name:
                        logs.append(f"📊 Busyness: {shop_name}")
                        resp = await session.call_tool("get_shop_busyness", {"shop_name": shop_name})
                    elif intent == "recommend" and location:
                        logs.append(f"📍 Location search: {location}")
                        resp = await session.call_tool("find_shops_by_location", {"location_name": location})
                    else:
                        logs.append("📦 Fetching all shops")
                        resp = await session.call_tool("list_all_shops", {})

                    raw = ""
                    if getattr(resp, "content", None):
                        c0 = resp.content[0]
                        raw = getattr(c0, "text", "") or getattr(c0, "data", "") or ""
                    logs.append(f"📦 Data: {len(raw)} bytes")
                    return raw or "[]"
        except Exception as e:
            logs.append(f"❌ MCP Error: {e}")
            return "[]"

    async def run_async(self, user_message: str) -> PipelineResult:
        """
        Pipeline:
          Router -> MCP fetch -> Analyst -> Advisor
        """
        logs: List[str] = []
        logs.append("🧠 Router agent: classifying...")
        intent_info = route(user_message)
        logs.append(
            f"   → intent={intent_info.get('intent')}, loc={intent_info.get('location')}, shop={intent_info.get('shop_name')}"
        )

        if intent_info.get("intent") == "general":
            logs.append("💡 General tips agent")
            result = general_tips(user_message)
            return PipelineResult(
                display_text=result.get("summary", ""),
                shops=[],
                intent_info=intent_info,
                logs=logs,
            )

        raw_data = await self.fetch_data_from_mcp(intent_info, logs)

        logs.append("📈 Analyst agent: chấm điểm...")
        sort_order = intent_info.get("sort_order", "best")
        apply_threshold = intent_info.get("intent") == "recommend"
        analyzed = analyze_shops(raw_data, sort_order=sort_order, apply_threshold=apply_threshold)

        if not analyzed:
            return PipelineResult(
                display_text="Không tìm thấy tiệm nào phù hợp. Hãy thử hỏi với tên quận hoặc tên tiệm cụ thể.",
                shops=[],
                intent_info=intent_info,
                logs=logs,
            )

        logs.append("🎯 Advisor agent: tạo câu trả lời...")
        result = advise(user_message, analyzed, intent_info)

        summary = result.get("summary", "")
        warnings = result.get("warnings") or []
        scores = result.get("scores")

        parts = [summary]
        if warnings:
            parts.append("\n**⚠️ Cảnh báo:**")
            for w in warnings:
                parts.append(f"- {w}")
        if scores:
            norm_scores = normalize_ai_scores({"scores": scores})
            trust = SafeWashEvaluator.calculate_trust_index(norm_scores)
            parts.append(f"\n📊 **Chỉ số Tin cậy SafeWash: {trust}/10**")

        return PipelineResult(
            display_text="\n".join(parts),
            shops=analyzed,
            intent_info=intent_info,
            logs=logs,
        )
