import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.agents import advise, analyze_shops, general_tips, route
from utils.helpers import safe_json_loads


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

    @staticmethod
    def _normalize_for_keyword(text: str) -> str:
        normalized = unicodedata.normalize("NFD", str(text or "").lower())
        stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        stripped = stripped.replace("đ", "d")
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return stripped

    async def fetch_data_from_mcp(self, intent_info: Dict[str, Any], logs: List[str]) -> str:
        """Call the appropriate MCP tool based on router intent."""
        intent = intent_info.get("intent", "general")
        location = intent_info.get("location")
        shop_name = intent_info.get("shop_name")

        try:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    logs.append("MCP connected")

                    if intent == "inspect" and shop_name:
                        logs.append(f"Inspecting: {shop_name}")
                        resp = await session.call_tool("get_audit_evidence", {"shop_name": shop_name})
                    elif intent == "compare" and shop_name:
                        logs.append(f"Comparing: {shop_name}")
                        resp = await session.call_tool("compare_shops", {"shop_names": shop_name})
                    elif intent == "busyness" and shop_name:
                        logs.append(f"Busyness: {shop_name}")
                        resp = await session.call_tool("get_shop_busyness", {"shop_name": shop_name})
                    elif intent == "recommend" and location:
                        logs.append(f"Location search: {location}")
                        resp = await session.call_tool("find_shops_by_location", {"location_name": location})
                    elif intent == "booking":
                        logs.append(f"Booking request: shop={shop_name or 'auto'}")
                        resp = await session.call_tool(
                            "schedule_shop_appointment",
                            {"request_text": intent_info.get("raw_message", ""), "shop_name": shop_name or ""},
                        )
                    else:
                        logs.append("Fetching all shops")
                        resp = await session.call_tool("list_all_shops", {})

                    raw = ""
                    if getattr(resp, "content", None):
                        c0 = resp.content[0]
                        raw = getattr(c0, "text", "") or getattr(c0, "data", "") or ""
                    logs.append(f"Data bytes: {len(raw)}")
                    return raw or "[]"
        except Exception as e:
            logs.append(f"MCP error: {e}")
            return "[]"

    @staticmethod
    def _build_top_pick_line(shop: Dict[str, Any]) -> str:
        name = str(shop.get("name") or "Tiệm phù hợp nhất").strip()
        metrics = shop.get("metrics", {}) if isinstance(shop.get("metrics"), dict) else {}
        services = shop.get("additional_info", {}).get("services", [])
        live_busyness = str(shop.get("live_busyness") or "").strip()

        def metric_value(key: str) -> int:
            raw = metrics.get(key, 0)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        reason = ""
        if isinstance(services, list) and services:
            first_service = str(services[0]).strip()
            if first_service:
                reason = f"Có dịch vụ nổi bật: {first_service}."
        if not reason:
            if metric_value("safe") > 0 and metric_value("clean") > 0:
                reason = "Được đánh giá tốt về độ an toàn và độ sạch."
            elif metric_value("safe") > 0:
                reason = "Được đánh giá tích cực về độ an toàn."
            elif metric_value("clean") > 0:
                reason = "Được đánh giá tốt về độ sạch và quy trình chăm xe."
            elif metric_value("support") > 0:
                reason = "Phản hồi khách hàng tích cực về thái độ phục vụ."
        if not reason and live_busyness:
            reason = f"Tình trạng hiện tại: {live_busyness}."
        if not reason:
            reason = "Phù hợp để ưu tiên tham khảo trước."

        return f"Gợi ý nổi bật nhất: {name}. {reason}"

    async def run_async(self, user_message: str) -> PipelineResult:
        """Pipeline: Router -> MCP fetch -> Analyst -> Advisor."""
        logs: List[str] = []
        logs.append("Router: classifying intent")
        intent_info = route(user_message)
        intent_info["raw_message"] = user_message
        logs.append(
            f"intent={intent_info.get('intent')}, loc={intent_info.get('location')}, shop={intent_info.get('shop_name')}"
        )

        if intent_info.get("intent") == "general":
            normalized_user = self._normalize_for_keyword(user_message)
            if any(k in normalized_user for k in ["dat lich", "hen lich", "booking", "book lich"]):
                intent_info["intent"] = "booking"
                logs.append("Intent override: booking (keyword fallback)")

        if intent_info.get("intent") == "general":
            logs.append("General tips")
            result = general_tips(user_message)
            return PipelineResult(
                display_text=(result.get("summary") or "").strip(),
                shops=[],
                intent_info=intent_info,
                logs=logs,
            )

        raw_data = await self.fetch_data_from_mcp(intent_info, logs)

        if intent_info.get("intent") == "booking":
            booking_payload = safe_json_loads(raw_data, {})
            if not isinstance(booking_payload, dict):
                booking_payload = {}
            booking_message = (
                booking_payload.get("message")
                or "Không thể đặt lịch lúc này. Bạn thử lại với tên tiệm và thời gian cụ thể nhé."
            )
            booking_shop = booking_payload.get("shop")
            return PipelineResult(
                display_text=str(booking_message).strip(),
                shops=[booking_shop] if isinstance(booking_shop, dict) else [],
                intent_info=intent_info,
                logs=logs,
            )

        logs.append("Analyst: scoring shops")
        sort_order = intent_info.get("sort_order", "best")
        apply_threshold = intent_info.get("intent") == "recommend"
        analyzed = analyze_shops(raw_data, sort_order=sort_order, apply_threshold=apply_threshold)

        if not analyzed:
            location = (intent_info.get("location") or "").strip()
            if intent_info.get("intent") == "recommend" and location:
                not_found_text = (
                    f"Không tìm thấy tiệm nào khớp khu vực '{location}' trong dữ liệu hiện tại. "
                    "Bạn thử khu vực lân cận hoặc nhập tên quận/huyện khác nhé."
                )
            else:
                not_found_text = "Không tìm thấy tiệm phù hợp. Hãy thử hỏi theo khu vực hoặc tên tiệm cụ thể."
            return PipelineResult(
                display_text=not_found_text,
                shops=[],
                intent_info=intent_info,
                logs=logs,
            )

        logs.append("Advisor: generating final response")
        result = advise(user_message, analyzed, intent_info)

        summary = (result.get("summary") or "").strip()
        warnings = result.get("warnings") or []

        parts: List[str] = []
        if summary:
            parts.append(summary)

        if (
            intent_info.get("intent") == "recommend"
            and intent_info.get("sort_order", "best") != "worst"
            and analyzed
        ):
            top_name = str(analyzed[0].get("name") or "").strip().lower()
            summary_lower = summary.lower() if summary else ""
            if (not summary) or (top_name and top_name not in summary_lower):
                top_pick_line = self._build_top_pick_line(analyzed[0])
                parts.insert(0, top_pick_line)

        if warnings:
            parts.append("\nLưu ý:")
            for w in warnings:
                parts.append(f"- {w}")

        display_text = "\n".join(parts).strip() or "Xin lỗi, hiện tại chưa có câu trả lời phù hợp."

        return PipelineResult(
            display_text=display_text,
            shops=analyzed,
            intent_info=intent_info,
            logs=logs,
        )
