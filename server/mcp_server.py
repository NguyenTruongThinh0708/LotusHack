from mcp.server.fastmcp import FastMCP
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

try:
    from pymongo import MongoClient
except Exception:  # pragma: no cover - optional dependency fallback
    MongoClient = None

load_dotenv()

mcp = FastMCP("WashGo-Intelligence-Hub")
_DEFAULT_STORE_COLLECTION_CANDIDATES = (
    "stores",
    "shops",
    "all_shops",
    "all_shops_final",
)
_mongo_client = None

_DISTRICT_RE = re.compile(r"\b(?:quan|q|district)\s*\.?\s*(\d{1,2})\b", flags=re.IGNORECASE)
_DISTRICT_COMPACT_RE = re.compile(r"\bq(\d{1,2})\b", flags=re.IGNORECASE)
_GENERIC_LOCATION_STOPWORDS = {
    "goi", "y", "dia", "diem", "rua", "xe", "tot", "nhat", "o", "tai", "khu",
    "vuc", "gan", "tiem", "shop", "car", "wash", "best", "recommend", "near",
    "cho", "toi", "minh", "xin", "hay", "can", "duoc", "khong", "ban", "ve",
    "tim", "kiem", "mot", "so", "khuvuc", "diachi", "quan", "huyen", "phuong",
    "thanh", "pho", "ho", "chi", "minh", "hcm", "tp", "tphcm",
}
_CRITERIA_TAG_LABELS = {
    "clean": "Sạch sẽ",
    "speed": "Nhanh",
    "price": "Giá tốt",
    "support": "Hỗ trợ tốt",
    "safe": "An toàn",
}
_DAY_NAME_MAP = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}
_BOOKING_DURATION_MIN = 60
_BOOKING_STEP_MIN = 30
_BOOKING_LOOKAHEAD_DAYS = 21


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _infer_db_name_from_uri(uri: str) -> str:
    try:
        parsed = urlparse(uri)
        path = (parsed.path or "").strip("/")
        if path:
            return path.split("/")[0]
    except Exception:
        return ""
    return ""


@lru_cache(maxsize=1)
def _get_mongo_db():
    global _mongo_client
    uri = str(os.getenv("MONGODB_URI", "")).strip()
    if not uri or MongoClient is None:
        return None

    db_name = (
        str(os.getenv("MONGODB_DB", "")).strip()
        or _infer_db_name_from_uri(uri)
        or "washgo"
    )
    timeout_ms = _env_int("MONGODB_TIMEOUT_MS", 3000)

    try:
        _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        _mongo_client.admin.command("ping")
        return _mongo_client[db_name]
    except Exception:
        _mongo_client = None
        return None


def _preferred_collection_candidates() -> list[str]:
    env_value = str(os.getenv("MONGODB_COLLECTION_CANDIDATES", "")).strip()
    from_env = [name.strip() for name in env_value.split(",") if name.strip()]
    combined = [*from_env, *_DEFAULT_STORE_COLLECTION_CANDIDATES]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in combined:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


@lru_cache(maxsize=8)
def _resolve_collection(env_key: str, default_name: str) -> Any:
    db = _get_mongo_db()
    if db is None:
        return None

    preferred_name = str(os.getenv(env_key, "")).strip() or default_name
    candidates = [preferred_name]
    if env_key == "MONGODB_COLLECTION":
        candidates.extend(_preferred_collection_candidates())

    seen: set[str] = set()
    deduped_candidates: list[str] = []
    for name in candidates:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_candidates.append(name)

    try:
        existing_names = [n for n in db.list_collection_names() if isinstance(n, str)]
    except Exception:
        existing_names = []

    for name in deduped_candidates:
        if name in existing_names:
            return db[name]

    for name in deduped_candidates:
        try:
            if db[name].estimated_document_count() > 0:
                return db[name]
        except Exception:
            continue

    if env_key == "MONGODB_COLLECTION":
        for name in existing_names:
            if name.startswith("system."):
                continue
            try:
                if db[name].estimated_document_count() > 0:
                    return db[name]
            except Exception:
                continue

    return db[preferred_name]


def _get_store_collection():
    return _resolve_collection("MONGODB_COLLECTION", "stores")


def _get_booking_collection():
    return _resolve_collection("MONGODB_BOOKING_COLLECTION", "bookings")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""

    normalized = unicodedata.normalize("NFD", raw)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("đ", "d")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalize_operating_hours(raw_hours: Any) -> dict[str, str]:
    if not isinstance(raw_hours, dict):
        return {}

    hours: dict[str, str] = {}
    for raw_day, raw_value in raw_hours.items():
        day_key = str(raw_day or "").strip().lower()
        day_name = _DAY_NAME_MAP.get(day_key, str(raw_day or "").strip())
        value = str(raw_value or "").replace("\u202f", " ").strip()
        if day_name and value:
            hours[day_name] = value
    return hours


def _normalize_popular_times(raw_popular_times: Any) -> list[dict]:
    if isinstance(raw_popular_times, list):
        out = []
        for row in raw_popular_times:
            if not isinstance(row, dict):
                continue
            day = str(row.get("day") or "").strip()
            time = str(row.get("time") or "").replace("\u202f", " ").strip()
            percent = _to_float(row.get("percent"), default=0.0)
            out.append(
                {
                    "day": day,
                    "time": time,
                    "percent": max(0, min(100, int(round(percent)))),
                }
            )
        return out

    if not isinstance(raw_popular_times, dict):
        return []

    out: list[dict] = []
    for day, entries in raw_popular_times.items():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            time = str(item.get("time") or "").replace("\u202f", " ").strip()
            percent = _to_float(item.get("percent"), default=0.0)
            out.append(
                {
                    "day": str(day or "").strip(),
                    "time": time,
                    "percent": max(0, min(100, int(round(percent)))),
                }
            )
    return out


def _extract_extensions(raw_extensions: Any) -> list[dict]:
    if not isinstance(raw_extensions, list):
        return []
    normalized_extensions: list[dict] = []
    for ext in raw_extensions:
        if not isinstance(ext, dict):
            continue
        cleaned_ext: dict[str, list[str]] = {}
        for key, value in ext.items():
            if isinstance(value, list):
                cleaned_vals = [
                    str(v).strip()
                    for v in value
                    if isinstance(v, str) and str(v).strip()
                ]
                if cleaned_vals:
                    cleaned_ext[str(key)] = cleaned_vals
        if cleaned_ext:
            normalized_extensions.append(cleaned_ext)
    return normalized_extensions


def _extract_services(record: dict) -> list[str]:
    services: list[str] = []

    type_value = record.get("type")
    if isinstance(type_value, str) and type_value.strip():
        services.append(type_value.strip())

    types = record.get("types")
    if isinstance(types, list):
        for value in types:
            if isinstance(value, str) and value.strip():
                services.append(value.strip())

    service_options = record.get("service_options")
    if isinstance(service_options, dict):
        for key, value in service_options.items():
            if value:
                services.append(str(key).replace("_", " ").strip())

    for ext in _extract_extensions(record.get("extensions")):
        for value_list in ext.values():
            services.extend(value_list)

    deduped: list[str] = []
    seen = set()
    for item in services:
        normalized = _normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def _normalize_top_reviews(raw_reviews: Any) -> list[dict]:
    if not isinstance(raw_reviews, list):
        return []

    normalized_reviews: list[dict] = []
    for review in raw_reviews:
        if not isinstance(review, dict):
            continue

        item: dict[str, Any] = {}
        text = str(review.get("text") or review.get("comment") or "").strip()
        if text:
            item["text"] = text

        rating = review.get("rating")
        if isinstance(rating, (int, float)):
            item["rating"] = rating

        images = review.get("images")
        if isinstance(images, list):
            valid_images = [
                str(img).strip()
                for img in images
                if isinstance(img, str) and str(img).strip()
            ]
            if valid_images:
                item["images"] = valid_images

        username = review.get("username")
        if isinstance(username, str) and username.strip():
            item["username"] = username.strip()

        likes = review.get("likes")
        if isinstance(likes, (int, float)):
            item["likes"] = likes

        if item:
            normalized_reviews.append(item)

    return normalized_reviews


def _infer_is_closed(record: dict) -> bool:
    state_text = f"{record.get('open_state', '')} {record.get('hours', '')}".lower()
    return "permanently closed" in state_text or "closed permanently" in state_text


def _normalize_metrics(record: dict) -> dict:
    source = record.get("metrics")
    if not isinstance(source, dict):
        source = record.get("store_metrics")
    if not isinstance(source, dict):
        source = {}

    metrics: dict[str, Any] = {
        key: _to_float(source.get(key), default=0.0) for key in _CRITERIA_TAG_LABELS
    }
    metrics["is_closed"] = bool(source.get("is_closed", False)) or _infer_is_closed(record)

    if "multi_service" in source:
        metrics["multi_service"] = bool(source.get("multi_service"))
    else:
        metrics["multi_service"] = len(_extract_services(record)) >= 3

    if "is_franchise" in source:
        metrics["is_franchise"] = bool(source.get("is_franchise"))
    else:
        title = str(record.get("title") or record.get("name") or "").lower()
        metrics["is_franchise"] = "vinawash" in title

    return metrics


def _build_criteria_tags(metrics: dict) -> list[str]:
    tags: list[str] = []
    for key, label in _CRITERIA_TAG_LABELS.items():
        if _to_float(metrics.get(key), default=0.0) >= 1.0:
            tags.append(label)
    return tags


def _normalize_shop_record(record: dict) -> dict:
    additional_info = record.get("additional_info")
    if not isinstance(additional_info, dict):
        additional_info = {}

    gps = record.get("gps_coordinates")
    if not isinstance(gps, dict):
        gps = {}

    services = additional_info.get("services")
    if not isinstance(services, list) or not services:
        services = _extract_services(record)

    shop_types = additional_info.get("type")
    if isinstance(shop_types, str):
        shop_types = [shop_types]
    if not isinstance(shop_types, list) or not shop_types:
        raw_type = record.get("type")
        if isinstance(raw_type, str) and raw_type.strip():
            shop_types = [raw_type.strip()]
        else:
            shop_types = []

    extensions = additional_info.get("extensions")
    if not isinstance(extensions, list) or not extensions:
        extensions = _extract_extensions(record.get("extensions"))

    metrics = _normalize_metrics(record)
    criteria_tags = _build_criteria_tags(metrics)

    working_hours = record.get("working_hours")
    if not isinstance(working_hours, dict) or not working_hours:
        working_hours = _normalize_operating_hours(record.get("operating_hours"))
    else:
        working_hours = _normalize_operating_hours(working_hours)

    busyness = record.get("busyness")
    if not isinstance(busyness, list) or not busyness:
        busyness = _normalize_popular_times(record.get("popular_times"))
    else:
        busyness = _normalize_popular_times(busyness)

    normalized = {
        "name": str(record.get("name") or record.get("title") or "").strip(),
        "latitude": _to_float(record.get("latitude", gps.get("latitude")), default=float("nan")),
        "longitude": _to_float(record.get("longitude", gps.get("longitude")), default=float("nan")),
        "working_hours": working_hours,
        "busyness": busyness,
        "live_busyness": str(
            record.get("live_busyness") or record.get("live_description") or record.get("open_state") or ""
        ).strip(),
        "phone": record.get("phone"),
        "website": record.get("website"),
        "metrics": metrics,
        "tags": criteria_tags,
        "top_reviews": _normalize_top_reviews(record.get("top_reviews")),
        "thumbnail": record.get("thumbnail"),
        "rating": _to_float(record.get("rating"), default=0.0),
        "reviews": int(_to_float(record.get("reviews"), default=0.0)),
        "additional_info": {
            "address": str(additional_info.get("address") or record.get("address") or "").strip(),
            "services": services,
            "type": shop_types,
            "extensions": extensions,
        },
        "place_id": record.get("place_id"),
    }

    if not (normalized["latitude"] == normalized["latitude"]):  # NaN check
        normalized["latitude"] = None
    if not (normalized["longitude"] == normalized["longitude"]):  # NaN check
        normalized["longitude"] = None

    return normalized


def load_db() -> list[dict]:
    collection = _get_store_collection()
    if collection is None:
        return []
    try:
        raw = list(collection.find({}))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [_normalize_shop_record(item) for item in raw if isinstance(item, dict)]


def _load_bookings() -> list[dict]:
    collection = _get_booking_collection()
    if collection is None:
        return []
    try:
        raw = list(collection.find({}, {"_id": 0}))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _save_bookings(bookings: list[dict]) -> None:
    collection = _get_booking_collection()
    if collection is None:
        return
    valid = [item for item in bookings if isinstance(item, dict)]
    try:
        collection.delete_many({})
        if valid:
            collection.insert_many(valid)
    except Exception:
        return


def _parse_12h_time_to_minutes(token: str) -> int | None:
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", token.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or "0")
    ampm = m.group(3).lower()
    if hour < 1 or hour > 12 or minute < 0 or minute > 59:
        return None
    if ampm == "am":
        if hour == 12:
            hour = 0
    elif hour != 12:
        hour += 12
    return hour * 60 + minute


def _parse_time_from_text(text: str) -> int | None:
    cleaned = str(text or "").replace("\u202f", " ")

    ampm_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", cleaned, flags=re.IGNORECASE)
    if ampm_match:
        token = f"{ampm_match.group(1)}:{ampm_match.group(2) or '00'} {ampm_match.group(3)}"
        return _parse_12h_time_to_minutes(token)

    hm_match = re.search(r"\b(\d{1,2})\s*[:h]\s*(\d{1,2})?\b", cleaned, flags=re.IGNORECASE)
    if hm_match:
        hour = int(hm_match.group(1))
        minute = int(hm_match.group(2) or "0")
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    return None


def _parse_date_from_text(text: str, now: datetime) -> tuple[date | None, bool]:
    raw = str(text or "")
    date_match = re.search(r"\b(\d{1,2})\s*[\/\-.]\s*(\d{1,2})\s*[\/\-.]\s*(\d{2,4})\b", raw)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).date(), True
        except ValueError:
            return None, False

    normalized = _normalize_text(raw)
    if re.search(r"\b(ngay mai|tomorrow)\b", normalized) or re.search(r"\bmai\b", normalized):
        return (now + timedelta(days=1)).date(), True
    if re.search(r"\b(hom nay|today)\b", normalized):
        return now.date(), True
    return None, False


def _extract_requested_datetime(request_text: str, now: datetime) -> dict[str, Any]:
    date_part, has_explicit_date = _parse_date_from_text(request_text, now)
    minute_part = _parse_time_from_text(request_text)
    has_explicit_time = minute_part is not None

    requested_dt = None
    if has_explicit_date and has_explicit_time and date_part is not None and minute_part is not None:
        requested_dt = datetime.combine(
            date_part,
            datetime.min.time(),
        ) + timedelta(minutes=minute_part)
    elif has_explicit_date and (not has_explicit_time) and date_part is not None:
        requested_dt = datetime.combine(date_part, datetime.min.time())
    elif (not has_explicit_date) and has_explicit_time and minute_part is not None:
        requested_dt = datetime.combine(now.date(), datetime.min.time()) + timedelta(minutes=minute_part)
        if requested_dt < now:
            requested_dt += timedelta(days=1)

    return {
        "requested_dt": requested_dt,
        "preferred_minute": minute_part,
        "has_explicit_date": has_explicit_date,
        "has_explicit_time": has_explicit_time,
    }


def _round_up_datetime(dt: datetime, step_min: int = _BOOKING_STEP_MIN) -> datetime:
    base = dt.replace(second=0, microsecond=0)
    if dt.second or dt.microsecond:
        base += timedelta(minutes=1)
    rem = base.minute % step_min
    if rem:
        base += timedelta(minutes=step_min - rem)
    return base


def _parse_working_window(range_text: str | None) -> dict[str, Any] | None:
    text = str(range_text or "").strip()
    if not text:
        return None
    low = text.lower()
    if "open 24 hours" in low:
        return {"all_day": True}
    if "closed" in low:
        return {"closed": True}

    normalized = re.sub(r"[–—−]", "-", text)
    normalized = normalized.replace("–", "-").replace("—", "-").replace("−", "-")
    tokens = re.findall(r"(\d{1,2}(?::\d{2})?\s*(?:AM|PM))", normalized, flags=re.IGNORECASE)
    if len(tokens) < 2:
        return None

    open_min = _parse_12h_time_to_minutes(tokens[0])
    close_min = _parse_12h_time_to_minutes(tokens[1])
    if open_min is None or close_min is None:
        return None
    return {"open_min": open_min, "close_min": close_min}


def _window_for_day(shop: dict, day_date: date) -> tuple[datetime, datetime] | None:
    hours = shop.get("working_hours", {})
    if not isinstance(hours, dict):
        hours = {}
    day_name = day_date.strftime("%A")
    parsed = _parse_working_window(hours.get(day_name))

    if parsed is None:
        # Fallback default when hours missing/unparseable.
        open_min, close_min = 8 * 60, 18 * 60
    elif parsed.get("closed"):
        return None
    elif parsed.get("all_day"):
        start = datetime.combine(day_date, datetime.min.time())
        end = start + timedelta(days=1)
        return start, end
    else:
        open_min = int(parsed["open_min"])
        close_min = int(parsed["close_min"])

    start = datetime.combine(day_date, datetime.min.time()) + timedelta(minutes=open_min)
    end = datetime.combine(day_date, datetime.min.time()) + timedelta(minutes=close_min)
    if close_min <= open_min:
        end += timedelta(days=1)
    return start, end


def _overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def _booking_conflict(bookings: list[dict], shop: dict, start: datetime, end: datetime) -> bool:
    place_id = str(shop.get("place_id") or "").strip()
    shop_name_norm = _normalize_text(shop.get("name", ""))
    for b in bookings:
        b_place_id = str(b.get("shop_place_id") or "").strip()
        b_shop_name = _normalize_text(b.get("shop_name", ""))
        same_shop = False
        if place_id and b_place_id:
            same_shop = place_id == b_place_id
        elif shop_name_norm and b_shop_name:
            same_shop = shop_name_norm == b_shop_name
        if not same_shop:
            continue

        try:
            existing_start = datetime.fromisoformat(str(b.get("start_iso")))
            existing_end = datetime.fromisoformat(str(b.get("end_iso")))
        except Exception:
            continue
        if _overlap(start, end, existing_start, existing_end):
            return True
    return False


def _find_shop_by_name(shops: list[dict], query_text: str) -> dict | None:
    query_norm = _normalize_text(query_text)
    if not query_norm or len(query_norm) < 2:
        return None

    best = None
    best_score = 0
    for shop in shops:
        name_norm = _normalize_text(shop.get("name", ""))
        if not name_norm:
            continue

        score = 0
        if query_norm == name_norm:
            score = 300
        elif f" {query_norm} " in f" {name_norm} ":
            score = 220
        elif query_norm in name_norm:
            score = 180
        else:
            tokens = [t for t in query_norm.split() if len(t) > 1]
            token_hits = sum(1 for t in tokens if f" {t} " in f" {name_norm} ")
            if token_hits >= max(2, len(tokens) - 1):
                score = 110 + token_hits * 10

        if score > best_score:
            best_score = score
            best = shop
    return best


def _pick_default_shop(shops: list[dict]) -> dict | None:
    if not shops:
        return None
    open_shops = [s for s in shops if not s.get("metrics", {}).get("is_closed", False)]
    source = open_shops if open_shops else shops
    return sorted(
        source,
        key=lambda s: (
            -_to_float(s.get("metrics", {}).get("safe"), default=0.0),
            -_to_float(s.get("metrics", {}).get("clean"), default=0.0),
            _normalize_text(s.get("name", "")),
        ),
    )[0]


def _resolve_shop_for_booking(shops: list[dict], shop_name: str, request_text: str) -> tuple[dict | None, bool]:
    by_shop_name = _find_shop_by_name(shops, shop_name)
    if by_shop_name is not None:
        return by_shop_name, False

    by_message = _find_shop_by_name(shops, request_text)
    if by_message is not None:
        return by_message, False

    return _pick_default_shop(shops), True


def _find_next_available_slot(
    shop: dict,
    bookings: list[dict],
    search_from: datetime,
) -> datetime | None:
    start_point = _round_up_datetime(search_from, _BOOKING_STEP_MIN)

    for day_offset in range(_BOOKING_LOOKAHEAD_DAYS + 1):
        day_date = (start_point + timedelta(days=day_offset)).date()
        window = _window_for_day(shop, day_date)
        if window is None:
            continue
        open_dt, close_dt = window

        cursor = open_dt
        if day_offset == 0 and start_point > cursor:
            cursor = start_point
        cursor = _round_up_datetime(cursor, _BOOKING_STEP_MIN)

        while cursor + timedelta(minutes=_BOOKING_DURATION_MIN) <= close_dt:
            end = cursor + timedelta(minutes=_BOOKING_DURATION_MIN)
            if not _booking_conflict(bookings, shop, cursor, end):
                return cursor
            cursor += timedelta(minutes=_BOOKING_STEP_MIN)
    return None


def _format_slot(dt: datetime) -> str:
    return dt.strftime("%H:%M %d/%m/%Y")


def _compact_shop_for_booking_response(shop: dict) -> dict:
    return {
        "name": shop.get("name"),
        "latitude": shop.get("latitude"),
        "longitude": shop.get("longitude"),
        "working_hours": shop.get("working_hours"),
        "busyness": shop.get("busyness"),
        "live_busyness": shop.get("live_busyness"),
        "phone": shop.get("phone"),
        "website": shop.get("website"),
        "metrics": shop.get("metrics"),
        "tags": shop.get("tags"),
        "top_reviews": (shop.get("top_reviews") or [])[:2],
        "thumbnail": shop.get("thumbnail"),
        "rating": shop.get("rating"),
        "reviews": shop.get("reviews"),
        "additional_info": shop.get("additional_info"),
        "place_id": shop.get("place_id"),
    }


def _extract_district_number(normalized_query: str) -> str | None:
    if not normalized_query:
        return None
    match = _DISTRICT_RE.search(normalized_query)
    if match:
        return match.group(1)
    compact_match = _DISTRICT_COMPACT_RE.search(normalized_query)
    if compact_match:
        return compact_match.group(1)
    return None


def _build_location_aliases(normalized_query: str) -> set[str]:
    aliases: set[str] = set()
    if normalized_query:
        aliases.add(normalized_query)

    district_no = _extract_district_number(normalized_query)
    if district_no:
        aliases.update({
            f"quan {district_no}",
            f"q {district_no}",
            f"q{district_no}",
            f"district {district_no}",
        })
    return {a for a in aliases if a}


def _prepare_query_tokens(normalized_query: str, district_no: str | None) -> list[str]:
    tokens = [t for t in normalized_query.split() if t]
    cleaned: list[str] = []
    for tok in tokens:
        if tok in _GENERIC_LOCATION_STOPWORDS:
            continue
        if tok in {"quan", "district", "q"}:
            continue
        if district_no and tok == district_no:
            continue
        if len(tok) == 1 and not tok.isdigit():
            continue
        cleaned.append(tok)

    return cleaned[:6]


def _location_score(
    *,
    query_tokens: list[str],
    aliases: set[str],
    district_no: str | None,
    searchable_text: str,
) -> int:
    if not searchable_text:
        return 0

    padded_text = f" {searchable_text} "
    score = 0

    for alias in aliases:
        alias_padded = f" {alias} "
        if alias_padded in padded_text:
            alias_score = 130 if district_no and district_no in alias else 110
            if len(alias.split()) <= 1:
                alias_score -= 20
            score = max(score, alias_score)
        elif alias in searchable_text:
            score = max(score, 70)

    if query_tokens:
        if len(query_tokens) >= 2:
            phrase = " ".join(query_tokens)
            if f" {phrase} " in padded_text:
                score = max(score, 88 + min(len(query_tokens), 4))
            else:
                exact_hits = sum(1 for tok in query_tokens if f" {tok} " in padded_text)
                if len(query_tokens) >= 3 and exact_hits >= len(query_tokens) - 1:
                    score = max(score, 60 + exact_hits * 2)
        else:
            if f" {query_tokens[0]} " in padded_text:
                score = max(score, 40)

    if district_no:
        district_pattern = rf"\b(?:quan|q|district)\s*{re.escape(district_no)}\b"
        if re.search(district_pattern, searchable_text, flags=re.IGNORECASE):
            score += 20

    return score


@mcp.tool()
def list_all_shops() -> str:
    """Return all normalized shops in the database."""
    return json.dumps(load_db(), ensure_ascii=False)


@mcp.tool()
def find_shops_by_location(location_name: str) -> str:
    """Search shops by district/address/name keyword."""
    data = load_db()
    normalized_query = _normalize_text(location_name)
    if not normalized_query:
        return json.dumps([], ensure_ascii=False)

    district_no = _extract_district_number(normalized_query)
    aliases = _build_location_aliases(normalized_query)
    query_tokens = _prepare_query_tokens(normalized_query, district_no)

    scored_results: list[tuple[int, dict]] = []
    for shop in data:
        name = _normalize_text(shop.get("name", ""))
        address = _normalize_text(shop.get("additional_info", {}).get("address", ""))
        searchable_text = f"{name} {address}".strip()
        score = _location_score(
            query_tokens=query_tokens,
            aliases=aliases,
            district_no=district_no,
            searchable_text=searchable_text,
        )
        if score > 0:
            scored_results.append((score, shop))

    scored_results.sort(
        key=lambda item: (
            -item[0],
            _normalize_text(item[1].get("name", "")),
        )
    )
    results = [shop for _, shop in scored_results]
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
def get_audit_evidence(shop_name: str) -> str:
    """Return detailed info for one shop by name."""
    data = load_db()
    query = _normalize_text(shop_name)
    for shop in data:
        if query and query in _normalize_text(shop.get("name", "")):
            return json.dumps(shop, ensure_ascii=False)
    return json.dumps({"error": "Shop not found"})


@mcp.tool()
def compare_shops(shop_names: str) -> str:
    """Compare multiple shops separated by |."""
    data = load_db()
    queries = [_normalize_text(q) for q in shop_names.split("|") if q.strip()]
    results = []
    for shop in data:
        name_norm = _normalize_text(shop.get("name", ""))
        for q in queries:
            if q and q in name_norm:
                results.append({
                    "name": shop.get("name"),
                    "address": shop.get("additional_info", {}).get("address", ""),
                    "metrics": shop.get("metrics", {}),
                    "reviews": [r.get("text", "") for r in shop.get("top_reviews", [])[:2]],
                    "phone": shop.get("phone"),
                    "working_hours": shop.get("working_hours"),
                    "tags": shop.get("tags", []),
                })
                break
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
def get_shop_busyness(shop_name: str) -> str:
    """Return busyness and opening-time info for one shop."""
    data = load_db()
    query = _normalize_text(shop_name)
    for shop in data:
        if query and query in _normalize_text(shop.get("name", "")):
            return json.dumps(shop, ensure_ascii=False)
    return json.dumps({"error": "Shop not found"})


@mcp.tool()
def schedule_shop_appointment(request_text: str, shop_name: str = "") -> str:
    """Create a basic booking slot for a shop using requested text/time."""
    shops = load_db()
    if not shops:
        return json.dumps(
            {
                "ok": False,
                "message": "Không có dữ liệu tiệm để đặt lịch.",
                "shop": None,
            },
            ensure_ascii=False,
        )

    now = datetime.now()
    selected_shop, auto_selected_shop = _resolve_shop_for_booking(shops, shop_name, request_text)
    if selected_shop is None:
        return json.dumps(
            {
                "ok": False,
                "message": "Không tìm thấy tiệm phù hợp để đặt lịch.",
                "shop": None,
            },
            ensure_ascii=False,
        )

    parsed = _extract_requested_datetime(request_text, now)
    bookings = _load_bookings()

    requested_dt = parsed.get("requested_dt")
    has_explicit_date = bool(parsed.get("has_explicit_date"))
    has_explicit_time = bool(parsed.get("has_explicit_time"))

    matched_requested = False
    chosen_slot = None

    if requested_dt is not None and has_explicit_date and has_explicit_time:
        candidate_start = requested_dt.replace(second=0, microsecond=0)
        candidate_end = candidate_start + timedelta(minutes=_BOOKING_DURATION_MIN)
        window = _window_for_day(selected_shop, candidate_start.date())
        in_window = (
            window is not None
            and candidate_start >= window[0]
            and candidate_end <= window[1]
        )
        no_conflict = not _booking_conflict(bookings, selected_shop, candidate_start, candidate_end)
        if in_window and no_conflict and candidate_start >= now:
            chosen_slot = candidate_start
            matched_requested = True
        else:
            chosen_slot = _find_next_available_slot(
                selected_shop,
                bookings,
                max(candidate_start, now + timedelta(minutes=5)),
            )
    else:
        seed = now + timedelta(minutes=5)
        if requested_dt is not None:
            seed = max(seed, requested_dt)
        chosen_slot = _find_next_available_slot(selected_shop, bookings, seed)

    if chosen_slot is None:
        return json.dumps(
            {
                "ok": False,
                "message": (
                    f"Không tìm thấy khung giờ trống trong {_BOOKING_LOOKAHEAD_DAYS} ngày tới cho "
                    f"{selected_shop.get('name', 'tiệm đã chọn')}."
                ),
                "shop": _compact_shop_for_booking_response(selected_shop),
            },
            ensure_ascii=False,
        )

    end_slot = chosen_slot + timedelta(minutes=_BOOKING_DURATION_MIN)
    booking_record = {
        "shop_place_id": selected_shop.get("place_id"),
        "shop_name": selected_shop.get("name"),
        "start_iso": chosen_slot.isoformat(),
        "end_iso": end_slot.isoformat(),
        "source_text": str(request_text or "").strip(),
        "created_at": now.isoformat(),
    }
    bookings.append(booking_record)
    _save_bookings(bookings)

    parts = []
    if auto_selected_shop:
        parts.append(
            f"Bạn chưa nêu rõ tên tiệm, hệ thống tạm chọn {selected_shop.get('name', 'tiệm phù hợp')}."
        )

    if matched_requested:
        parts.append(
            f"Đã đặt lịch thành công lúc {_format_slot(chosen_slot)} cho {selected_shop.get('name', 'tiệm đã chọn')}."
        )
    elif has_explicit_date and has_explicit_time:
        parts.append(
            "Khung giờ bạn yêu cầu chưa phù hợp/đã kín lịch, mình đã chọn khung gần nhất."
        )
        parts.append(
            f"Lịch mới: {_format_slot(chosen_slot)} tại {selected_shop.get('name', 'tiệm đã chọn')}."
        )
    else:
        parts.append(
            "Bạn chưa nói rõ ngày giờ, mình đã đặt khung trống gần nhất theo giờ làm việc của tiệm."
        )
        parts.append(
            f"Lịch hẹn: {_format_slot(chosen_slot)} tại {selected_shop.get('name', 'tiệm đã chọn')}."
        )

    return json.dumps(
        {
            "ok": True,
            "message": " ".join(parts).strip(),
            "shop": _compact_shop_for_booking_response(selected_shop),
            "booking": {
                "start_iso": chosen_slot.isoformat(),
                "end_iso": end_slot.isoformat(),
            },
            "matched_requested": matched_requested,
            "auto_selected_shop": auto_selected_shop,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def get_safest_shops(limit: int = 5) -> str:
    """Return top safe shops by safe metric."""
    data = load_db()
    open_shops = [s for s in data if not s.get("metrics", {}).get("is_closed", False)]
    open_shops.sort(key=lambda s: s.get("metrics", {}).get("safe", 0), reverse=True)
    top = open_shops[:limit]
    results = []
    for shop in top:
        results.append({
            "name": shop.get("name"),
            "address": shop.get("additional_info", {}).get("address", ""),
            "metrics": shop.get("metrics", {}),
            "phone": shop.get("phone"),
            "tags": shop.get("tags", []),
        })
    return json.dumps(results, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
