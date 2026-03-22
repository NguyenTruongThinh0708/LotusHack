import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Dict, List
from urllib import error as urlerror
from urllib import request as urlrequest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.agents import advise, analyze_shops, general_tips, route
from utils.helpers import safe_json_loads

TAG_DISPLAY_LABELS = {
    "clean": "Sạch sẽ",
    "speed": "Nhanh",
    "price": "Giá tốt",
    "support": "Hỗ trợ tốt",
    "safe": "An toàn",
}

TAG_KEYWORDS = {
    "clean": {"sach", "sach se", "sieu sach", "clean", "ve sinh"},
    "speed": {"nhanh", "sieu nhanh", "fast", "quick", "gap"},
    "price": {"gia re", "gia tot", "hop ly", "cheap", "budget", "rat tot"},
    "support": {"ho tro", "phuc vu", "service", "than thien", "xuat sac"},
    "safe": {"an toan", "safe", "uy tin", "bao ve"},
}


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
        self.osrm_base = str(os.getenv("OSRM_BASE", "https://router.project-osrm.org")).rstrip("/")
        self.osrm_timeout_s = max(1.0, float(os.getenv("OSRM_TIMEOUT_S", "4")))
        self.osrm_cache_ttl_s = max(30, int(os.getenv("OSRM_CACHE_TTL_S", "600")))
        self.osrm_min_request_gap_s = max(0.0, float(os.getenv("OSRM_MIN_REQUEST_GAP_S", "1.5")))
        self.osrm_cooldown_on_limit_s = max(3, int(os.getenv("OSRM_COOLDOWN_ON_LIMIT_S", "30")))
        self._osrm_cache: dict[str, dict[str, float]] = {}
        self._osrm_last_request_at = 0.0
        self._osrm_cooldown_until = 0.0

    @staticmethod
    def _normalize_for_keyword(text: str) -> str:
        normalized = unicodedata.normalize("NFD", str(text or "").lower())
        stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        stripped = stripped.replace("đ", "d")
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return stripped

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * r * asin(sqrt(a))

    @staticmethod
    def _looks_like_nearby_query(user_message: str) -> bool:
        text = SafeWashPipeline._normalize_for_keyword(user_message)
        keywords = (
            "gan day",
            "gan nhat",
            "xung quanh",
            "o gan toi",
            "near me",
            "nearby",
            "nearest",
            "closest",
        )
        return any(k in text for k in keywords)

    @staticmethod
    def _match_keyword(normalized_text: str, keyword: str) -> bool:
        if not normalized_text or not keyword:
            return False
        padded = f" {normalized_text} "
        return f" {keyword} " in padded

    def _extract_requested_tag_keys(self, user_message: str) -> list[str]:
        normalized = self._normalize_for_keyword(user_message)
        if not normalized:
            return []
        requested: list[str] = []
        for key, keywords in TAG_KEYWORDS.items():
            if any(self._match_keyword(normalized, kw) for kw in keywords):
                requested.append(key)
        return requested

    @staticmethod
    def _normalize_feature_phrase(text: str) -> str:
        normalized = SafeWashPipeline._normalize_for_keyword(text)
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _iter_shop_special_features(self, shop: dict) -> list[str]:
        raw_features: Any = shop.get("special_features")
        additional_info = shop.get("additional_info")
        if (not raw_features) and isinstance(additional_info, dict):
            raw_features = additional_info.get("special_features")

        collected: list[str] = []
        if isinstance(raw_features, str):
            value = raw_features.strip()
            if value:
                collected.append(value)
        elif isinstance(raw_features, list):
            for item in raw_features:
                if isinstance(item, str):
                    value = item.strip()
                    if value:
                        collected.append(value)
                elif isinstance(item, dict):
                    value = str(item.get("name") or item.get("feature") or "").strip()
                    if value:
                        collected.append(value)
        elif isinstance(raw_features, dict):
            for key, value in raw_features.items():
                if isinstance(value, bool) and not value:
                    continue
                label = str(key or "").strip()
                if label:
                    collected.append(label)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in collected:
            normalized = self._normalize_feature_phrase(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(item)
        return deduped

    def _extract_requested_special_features(self, user_message: str, shops: list[dict]) -> list[str]:
        normalized_user = self._normalize_feature_phrase(user_message)
        if not normalized_user or not shops:
            return []

        feature_lookup: dict[str, str] = {}
        for shop in shops:
            for feature in self._iter_shop_special_features(shop):
                normalized = self._normalize_feature_phrase(feature)
                if normalized and normalized not in feature_lookup:
                    feature_lookup[normalized] = feature

        requested: list[str] = []
        for normalized, original in sorted(
            feature_lookup.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if self._match_keyword(normalized_user, normalized):
                requested.append(original)
        return requested

    def _shop_tag_match_count(self, shop: dict, requested_keys: list[str]) -> int:
        if not requested_keys:
            return 0

        metrics = shop.get("metrics") if isinstance(shop.get("metrics"), dict) else {}
        tags = shop.get("tags") if isinstance(shop.get("tags"), list) else []
        normalized_tags = " ".join(
            self._normalize_for_keyword(str(tag)) for tag in tags if isinstance(tag, str)
        )

        matched = 0
        for key in requested_keys:
            score = self._to_float(metrics.get(key))
            metric_ok = score is not None and score >= 1
            tag_ok = any(self._match_keyword(normalized_tags, kw) for kw in TAG_KEYWORDS.get(key, set()))
            if metric_ok or tag_ok:
                matched += 1
        return matched

    def _shop_matches_tags(self, shop: dict, requested_keys: list[str]) -> bool:
        if not requested_keys:
            return True
        return self._shop_tag_match_count(shop, requested_keys) >= len(requested_keys)

    def _shop_special_feature_match_count(self, shop: dict, requested_features: list[str]) -> int:
        if not requested_features:
            return 0

        available: set[str] = set()
        for item in self._iter_shop_special_features(shop):
            normalized_item = self._normalize_feature_phrase(item)
            if normalized_item:
                available.add(normalized_item)
        if not available:
            return 0

        matched = 0
        for feature in requested_features:
            normalized = self._normalize_feature_phrase(feature)
            if normalized and normalized in available:
                matched += 1
        return matched

    def _filter_shops_by_requested_tags(
        self,
        shops: list[dict],
        user_message: str,
        intent_info: dict,
        logs: list[str],
    ) -> list[dict]:
        requested_keys = self._extract_requested_tag_keys(user_message)
        requested_special_features = self._extract_requested_special_features(user_message, shops)
        if not requested_keys and not requested_special_features:
            return shops

        if requested_keys:
            intent_info["requested_tags"] = requested_keys
        if requested_special_features:
            intent_info["requested_special_features"] = requested_special_features

        filtered: list[dict] = []
        for shop in shops:
            tag_match_count = self._shop_tag_match_count(shop, requested_keys)
            special_feature_match_count = self._shop_special_feature_match_count(
                shop,
                requested_special_features,
            )
            tag_ok = (not requested_keys) or (tag_match_count >= len(requested_keys))
            special_feature_ok = (not requested_special_features) or (special_feature_match_count > 0)
            if not tag_ok or not special_feature_ok:
                continue
            enriched = dict(shop)
            enriched["_tag_match_count"] = tag_match_count
            enriched["_special_feature_match_count"] = special_feature_match_count
            filtered.append(enriched)

        labels = [TAG_DISPLAY_LABELS.get(k, k) for k in requested_keys]
        criteria_parts: list[str] = []
        if labels:
            criteria_parts.append(f"tags={', '.join(labels)}")
        if requested_special_features:
            criteria_parts.append(f"special_features={', '.join(requested_special_features)}")
        criteria_summary = "; ".join(criteria_parts) if criteria_parts else "criteria"
        if filtered:
            filtered.sort(
                key=lambda s: (
                    -int(s.get("_special_feature_match_count", 0)),
                    -int(s.get("_tag_match_count", 0)),
                    s.get("_risk") == "CLOSED",
                    -float(s.get("_trust", 0)),
                )
            )
            logs.append(
                f"Tag filter applied ({criteria_summary}) -> {len(filtered)}/{len(shops)} shops."
            )
            return filtered

        logs.append(f"Tag filter applied ({criteria_summary}) -> 0 match.")
        return []

    @staticmethod
    def _origin_key(lat: float, lng: float) -> str:
        return f"{lat:.3f},{lng:.3f}"

    @staticmethod
    def _shop_key(lat: float, lng: float) -> str:
        return f"{lat:.6f},{lng:.6f}"

    def _cache_key(self, user_lat: float, user_lng: float, shop_lat: float, shop_lng: float) -> str:
        return f"{self._origin_key(user_lat, user_lng)}|{self._shop_key(shop_lat, shop_lng)}"

    def _get_cached_osrm_meters(self, cache_key: str) -> float | None:
        now = time.time()
        record = self._osrm_cache.get(cache_key)
        if not isinstance(record, dict):
            return None
        expires_at = float(record.get("expires_at", 0.0))
        if expires_at <= now:
            self._osrm_cache.pop(cache_key, None)
            return None
        meters = self._to_float(record.get("meters"))
        if meters is None:
            return None
        return meters

    def _set_cached_osrm_meters(self, cache_key: str, meters: float, ttl_s: int | None = None) -> None:
        ttl = ttl_s if ttl_s is not None else self.osrm_cache_ttl_s
        self._osrm_cache[cache_key] = {
            "meters": float(meters),
            "expires_at": time.time() + max(3, int(ttl)),
        }

    def _fetch_osrm_meters_batch(
        self,
        user_lat: float,
        user_lng: float,
        candidates: list[tuple[str, float, float]],
        logs: list[str],
    ) -> dict[str, float]:
        if not candidates:
            return {}

        now = time.time()
        if now < self._osrm_cooldown_until:
            remaining = int(round(self._osrm_cooldown_until - now))
            logs.append(f"OSRM cooldown active, skip request ({remaining}s left).")
            return {}
        if now - self._osrm_last_request_at < self.osrm_min_request_gap_s:
            logs.append("OSRM request throttled by min gap, using cache/fallback for now.")
            return {}

        source = f"{user_lng},{user_lat}"
        dest_string = ";".join(f"{lng},{lat}" for _, lat, lng in candidates)
        coordinates = f"{source};{dest_string}"
        endpoint = f"{self.osrm_base}/table/v1/driving/{coordinates}?sources=0&annotations=distance"

        try:
            self._osrm_last_request_at = time.time()
            req = urlrequest.Request(
                endpoint,
                headers={"User-Agent": "WashGo/1.0"},
                method="GET",
            )
            with urlrequest.urlopen(req, timeout=self.osrm_timeout_s) as resp:
                status = int(resp.status)
                payload_bytes = resp.read()
        except urlerror.HTTPError as e:
            if int(getattr(e, "code", 0)) == 429:
                self._osrm_cooldown_until = time.time() + self.osrm_cooldown_on_limit_s
                logs.append("OSRM rate-limited (429), entering cooldown.")
            else:
                logs.append(f"OSRM HTTP error: {getattr(e, 'code', 'unknown')}")
            return {}
        except Exception as e:
            logs.append(f"OSRM request failed: {e}")
            return {}

        if status == 429:
            self._osrm_cooldown_until = time.time() + self.osrm_cooldown_on_limit_s
            logs.append("OSRM rate-limited (429), entering cooldown.")
            return {}
        if status < 200 or status >= 300:
            logs.append(f"OSRM failed with status {status}.")
            return {}

        try:
            data = json.loads(payload_bytes.decode("utf-8", errors="ignore"))
        except Exception:
            logs.append("OSRM response is not valid JSON.")
            return {}

        row = data.get("distances", [])
        if not isinstance(row, list) or not row:
            logs.append("OSRM response missing distances.")
            return {}
        first_row = row[0]
        if not isinstance(first_row, list):
            logs.append("OSRM response has invalid distances row.")
            return {}

        resolved: dict[str, float] = {}
        for idx, (cache_key, _lat, _lng) in enumerate(candidates, start=1):
            meters = self._to_float(first_row[idx] if idx < len(first_row) else None)
            if meters is None or meters <= 0:
                # Cache invalid briefly to avoid hammering when route data is missing.
                self._set_cached_osrm_meters(cache_key, -1.0, ttl_s=60)
                continue
            resolved[cache_key] = meters
            self._set_cached_osrm_meters(cache_key, meters)
        return resolved

    def _sort_by_nearest_if_needed(
        self,
        shops: list[dict],
        user_message: str,
        user_coords: dict | None,
        intent_info: dict,
        logs: list[str],
    ) -> list[dict]:
        if not shops:
            return shops
        if intent_info.get("intent") != "recommend":
            return shops
        if intent_info.get("sort_order", "best") == "worst":
            return shops
        nearby_raw = intent_info.get("nearby")
        nearby_from_router = nearby_raw is True or str(nearby_raw).strip().lower() in {"1", "true", "yes"}
        if (not nearby_from_router) and (not self._looks_like_nearby_query(user_message)):
            return shops

        if not isinstance(user_coords, dict):
            logs.append("Nearby query detected but user coordinates are missing.")
            intent_info["needs_user_location"] = True
            return shops

        user_lat = self._to_float(user_coords.get("lat"))
        user_lng = self._to_float(user_coords.get("lng"))
        if user_lat is None or user_lng is None:
            logs.append("Nearby query detected but user coordinates are invalid.")
            intent_info["needs_user_location"] = True
            return shops

        with_distance: list[dict] = []
        without_distance: list[dict] = []
        unresolved: list[tuple[str, float, float, dict]] = []
        for shop in shops:
            lat = self._to_float(shop.get("latitude"))
            lng = self._to_float(shop.get("longitude"))
            if lat is None or lng is None:
                without_distance.append(shop)
                continue

            enriched = dict(shop)
            cache_key = self._cache_key(user_lat, user_lng, lat, lng)
            cached_meters = self._get_cached_osrm_meters(cache_key)
            if cached_meters is not None:
                if cached_meters > 0:
                    enriched["_distance_m"] = round(cached_meters, 1)
                    enriched["_distance_km"] = round(cached_meters / 1000.0, 2)
                    enriched["_distance_source"] = "osrm"
                else:
                    fallback_km = self._haversine_km(user_lat, user_lng, lat, lng)
                    enriched["_distance_m"] = round(fallback_km * 1000.0, 1)
                    enriched["_distance_km"] = round(fallback_km, 2)
                    enriched["_distance_source"] = "haversine-fallback"
            else:
                unresolved.append((cache_key, lat, lng, enriched))
            with_distance.append(enriched)

        if unresolved:
            batch_candidates = [(key, lat, lng) for key, lat, lng, _ in unresolved]
            fetched = self._fetch_osrm_meters_batch(
                user_lat=user_lat,
                user_lng=user_lng,
                candidates=batch_candidates,
                logs=logs,
            )
            for cache_key, lat, lng, enriched in unresolved:
                meters = fetched.get(cache_key)
                if meters is not None and meters > 0:
                    enriched["_distance_m"] = round(meters, 1)
                    enriched["_distance_km"] = round(meters / 1000.0, 2)
                    enriched["_distance_source"] = "osrm"
                    continue

                # Fallback to haversine if OSRM is temporarily unavailable.
                fallback_km = self._haversine_km(user_lat, user_lng, lat, lng)
                enriched["_distance_m"] = round(fallback_km * 1000.0, 1)
                enriched["_distance_km"] = round(fallback_km, 2)
                enriched["_distance_source"] = "haversine-fallback"

        if not with_distance:
            logs.append("Nearby query detected but no shop has valid coordinates.")
            return shops

        with_distance.sort(
            key=lambda s: (
                s.get("_risk") == "CLOSED",
                0 if s.get("_distance_source") == "osrm" else 1,
                float(s.get("_distance_km", 10**9)),
                -float(s.get("_trust", 0)),
            )
        )
        intent_info["sort_mode"] = "nearest"
        osrm_count = sum(1 for s in with_distance if s.get("_distance_source") == "osrm")
        fallback_count = max(0, len(with_distance) - osrm_count)
        logs.append(
            f"Nearby ranking applied with OSRM: {osrm_count}/{len(with_distance)} shops (fallback: {fallback_count})."
        )
        return with_distance + without_distance

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
        distance_km = SafeWashPipeline._to_float(shop.get("_distance_km"))

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
        if distance_km is not None and distance_km >= 0:
            reason = f"Cách vị trí của bạn khoảng {distance_km:.1f} km. {reason}"

        return f"Gợi ý nổi bật nhất: {name}. {reason}"

    async def run_async(self, user_message: str, user_coords: Dict[str, Any] | None = None) -> PipelineResult:
        """Pipeline: Router -> MCP fetch -> Analyst -> Advisor."""
        logs: List[str] = []
        logs.append("Router: classifying intent")
        intent_info = route(user_message)
        intent_info["raw_message"] = user_message
        if isinstance(user_coords, dict):
            intent_info["user_coords"] = {
                "lat": user_coords.get("lat"),
                "lng": user_coords.get("lng"),
                "accuracy": user_coords.get("accuracy"),
            }
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
        analyzed = self._filter_shops_by_requested_tags(
            analyzed,
            user_message=user_message,
            intent_info=intent_info,
            logs=logs,
        )
        analyzed = self._sort_by_nearest_if_needed(
            analyzed,
            user_message=user_message,
            user_coords=user_coords,
            intent_info=intent_info,
            logs=logs,
        )

        if not analyzed:
            location = (intent_info.get("location") or "").strip()
            requested_tags = intent_info.get("requested_tags") or []
            requested_special_features = intent_info.get("requested_special_features") or []
            tag_labels = [
                TAG_DISPLAY_LABELS.get(str(tag), str(tag))
                for tag in requested_tags
                if str(tag).strip()
            ]
            special_feature_labels = [
                str(feature).strip()
                for feature in requested_special_features
                if str(feature).strip()
            ]
            criteria_labels: list[str] = []
            if tag_labels:
                criteria_labels.append(f"tag: {', '.join(tag_labels)}")
            if special_feature_labels:
                criteria_labels.append(f"special feature: {', '.join(special_feature_labels)}")
            criteria_text = " + ".join(criteria_labels)
            if intent_info.get("intent") == "recommend" and location:
                if criteria_text:
                    not_found_text = (
                        f"Không tìm thấy tiệm khớp khu vực '{location}' với tiêu chí {criteria_text}. "
                        "Bạn thử nới tiêu chí hoặc đổi khu vực nhé."
                    )
                else:
                    not_found_text = (
                        f"Không tìm thấy tiệm nào khớp khu vực '{location}' trong dữ liệu hiện tại. "
                        "Bạn thử khu vực lân cận hoặc nhập tên quận/huyện khác nhé."
                    )
            else:
                if criteria_text:
                    not_found_text = (
                        f"Không tìm thấy tiệm phù hợp với {criteria_text}. "
                        "Bạn thử đổi tiêu chí (ví dụ sạch/nhanh/giá tốt) hoặc thêm khu vực nhé."
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
        if intent_info.get("needs_user_location"):
            warnings = [
                *warnings,
                "Mình chưa lấy được vị trí hiện tại, nên đang gợi ý theo chất lượng tổng thể. Hãy bật định vị để ưu tiên tiệm gần nhất.",
            ]

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
