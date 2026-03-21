import streamlit as st
import os
import asyncio
import json
import sys
import io
import wave
import hashlib
import nest_asyncio
from pathlib import Path
from dotenv import load_dotenv

# CRITICAL: load .env BEFORE any core imports (agents.py needs OPENAI_API_KEY)
load_dotenv()
nest_asyncio.apply()

# MCP
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Core
from core.evaluator import SafeWashEvaluator
from core.agents import route, analyze_shops, advise, general_tips
from core.voice_engine import blaze_stt, blaze_tts, openai_stt
from utils.helpers import safe_json_loads, normalize_ai_scores

# --- CONFIG ---
if not os.getenv("OPENAI_API_KEY"):
    st.error("⚠️ Thiếu OPENAI_API_KEY trong .env. Vui lòng cấu hình trước khi chạy.")
    st.stop()

SERVER_SCRIPT = Path(__file__).parent / "server" / "mcp_server.py"
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(SERVER_SCRIPT)],
    env=os.environ.copy(),
)

# --- PAGE CONFIG ---
st.set_page_config(page_title="WashGo AI - Đánh Giá Rửa Xe", page_icon="🛡️", layout="wide")

# --- SESSION STATE ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "recommendations" not in st.session_state:
    st.session_state.recommendations = []
if "logs" not in st.session_state:
    st.session_state.logs = []
if "last_intent" not in st.session_state:
    st.session_state.last_intent = None
if "voice_enabled" not in st.session_state:
    st.session_state.voice_enabled = bool(os.getenv("BLAZE_API_KEY"))
if "selected_shop" not in st.session_state:
    st.session_state.selected_shop = None
if "last_mic_signature" not in st.session_state:
    st.session_state.last_mic_signature = None
if "last_stt_engine" not in st.session_state:
    st.session_state.last_stt_engine = None


def estimate_wav_duration_seconds(audio_bytes: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return round(frames / float(rate), 2)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
#  MCP DATA LAYER — call the right tool based on intent
# ─────────────────────────────────────────────────────────────
async def fetch_data_from_mcp(intent_info: dict) -> str:
    """Call the appropriate MCP tool based on router's intent classification."""
    intent = intent_info.get("intent", "general")
    location = intent_info.get("location")
    shop_name = intent_info.get("shop_name")

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                st.session_state.logs.append("✅ MCP connected")

                # Pick the right tool
                if intent == "inspect" and shop_name:
                    st.session_state.logs.append(f"🔍 Inspecting: {shop_name}")
                    resp = await session.call_tool("get_audit_evidence", {"shop_name": shop_name})
                elif intent == "compare" and shop_name:
                    st.session_state.logs.append(f"⚖️ Comparing: {shop_name}")
                    resp = await session.call_tool("compare_shops", {"shop_names": shop_name})
                elif intent == "busyness" and shop_name:
                    st.session_state.logs.append(f"📊 Busyness: {shop_name}")
                    resp = await session.call_tool("get_shop_busyness", {"shop_name": shop_name})
                elif intent == "recommend" and location:
                    st.session_state.logs.append(f"📍 Location search: {location}")
                    resp = await session.call_tool("find_shops_by_location", {"location_name": location})
                else:
                    st.session_state.logs.append("📦 Fetching all shops")
                    resp = await session.call_tool("list_all_shops", {})

                raw = ""
                if getattr(resp, "content", None):
                    c0 = resp.content[0]
                    raw = getattr(c0, "text", "") or getattr(c0, "data", "") or ""
                st.session_state.logs.append(f"📦 Data: {len(raw)} bytes")
                return raw or "[]"
    except Exception as e:
        st.session_state.logs.append(f"❌ MCP Error: {e}")
        return "[]"


# ─────────────────────────────────────────────────────────────
#  ORCHESTRATOR — runs the 3-agent pipeline
# ─────────────────────────────────────────────────────────────
def run_pipeline(user_message: str) -> tuple[str, list[dict], dict]:
    """
    Returns (display_text, shop_cards, intent_info).
    Pipeline: Router → MCP fetch → Analyst → Advisor
    """
    # STEP 1: Router classifies intent
    st.session_state.logs.append("🧠 Router agent: classifying...")
    intent_info = route(user_message)
    st.session_state.logs.append(f"   → intent={intent_info.get('intent')}, loc={intent_info.get('location')}, shop={intent_info.get('shop_name')}")

    # STEP 1b: General questions skip data fetch
    if intent_info.get("intent") == "general":
        st.session_state.logs.append("💡 General tips agent")
        result = general_tips(user_message)
        return result.get("summary", ""), [], intent_info

    # STEP 2: Fetch data via MCP
    raw_data = asyncio.run(fetch_data_from_mcp(intent_info))

    # STEP 3: Analyst enriches data
    st.session_state.logs.append("📈 Analyst agent: chấm điểm...")
    sort_order = intent_info.get("sort_order", "best")
    apply_threshold = intent_info.get("intent") == "recommend"
    analyzed = analyze_shops(raw_data, sort_order=sort_order, apply_threshold=apply_threshold)

    if not analyzed:
        return "Không tìm thấy tiệm nào phù hợp. Hãy thử hỏi với tên quận hoặc tên tiệm cụ thể.", [], intent_info

    # STEP 4: Advisor generates response
    st.session_state.logs.append("🎯 Advisor agent: tạo câu trả lời...")
    result = advise(user_message, analyzed, intent_info)

    # Build display
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
        parts.append(f"\n📊 **Chỉ số Tin cậy WashGo: {trust}/10**")

    display_text = "\n".join(parts)
    return display_text, analyzed, intent_info


# ═══════════════════════════════════════════════════════════════
#                           UI
# ═══════════════════════════════════════════════════════════════

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/car-wash.png", width=64)
    st.title("WashGo AI")
    st.caption("Hệ thống đánh giá an toàn tiệm rửa xe thông minh")
    st.divider()

    # Voice toggle
    has_voice_key = bool(os.getenv("BLAZE_API_KEY"))
    if has_voice_key:
        st.session_state.voice_enabled = st.toggle(
            "🎙️ Giọng nói", value=st.session_state.voice_enabled
        )
        if st.session_state.last_stt_engine:
            st.caption(f"STT gần nhất: {st.session_state.last_stt_engine}")
    else:
        st.session_state.voice_enabled = False

    st.divider()
    st.markdown("### 🤖 Kiến trúc Đa Agent")
    st.markdown("""
    - **Router Agent** — Phân loại ý định
    - **Analyst Agent** — Chấm điểm & phân tích
    - **Advisor Agent** — Tư vấn & đề xuất
    - **Voice Agent** — STT (Blaze + OpenAI fallback) ↔ TTS
    """)
    st.divider()

    st.markdown("### 💡 Gợi ý nhanh")
    suggestions = [
        "Tiệm rửa xe an toàn nhất?",
        "Tiệm nào tốt ở Tân Phú?",
        "So sánh VinaWash và ProWash",
        "VinaWash đông nhất lúc nào?",
        "Mẹo bảo vệ sơn xe khi rửa?",
    ]
    for s in suggestions:
        if st.button(s, key=f"sug_{s}", use_container_width=True):
            st.session_state["_pending_input"] = s

    st.divider()
    with st.expander("🛠️ Nhật ký Agent"):
        for log in st.session_state.logs:
            st.caption(log)
    if st.button("🗑️ Xoá lịch sử chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.recommendations = []
        st.session_state.logs = []
        st.session_state.last_intent = None
        st.session_state.selected_shop = None
        st.session_state.last_mic_signature = None
        st.session_state.last_stt_engine = None
        st.rerun()

# --- HEADER ---
st.title("🛡️ WashGo AI: Chuyên gia Đánh giá")

if st.session_state.last_intent:
    intent_map = {
        "recommend": "🔎 Đang đề xuất",
        "compare": "⚖️ Đang so sánh",
        "inspect": "🔬 Đang kiểm tra",
        "busyness": "📊 Giờ cao điểm",
        "general": "💡 Mẹo chung",
    }
    label = intent_map.get(st.session_state.last_intent, "💬")
    st.caption(f"Chế độ: {label}")

# --- RENDER CHAT ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- INPUT ---
pending = st.session_state.pop("_pending_input", None)

# Voice input via mic icon
voice_text = None
if st.session_state.voice_enabled:
    try:
        from streamlit_mic_recorder import mic_recorder
        audio_data = mic_recorder(
            start_prompt="🎙️",
            stop_prompt="⏹️",
            key="voice_input",
            just_once=False,
            format="wav",
        )
        if audio_data and audio_data.get("bytes"):
            audio_bytes = audio_data.get("bytes", b"")
            audio_hash = hashlib.sha1(audio_bytes).hexdigest()[:16] if audio_bytes else "none"
            audio_signature = f"{audio_data.get('id')}:{audio_hash}"

            if audio_signature != st.session_state.last_mic_signature:
                st.session_state.last_mic_signature = audio_signature
                with st.spinner("🎧 Đang nhận dạng giọng nói..."):
                    audio_seconds = estimate_wav_duration_seconds(audio_bytes)
                    st.session_state.logs.append(
                        f"🎛️ Mic meta: id={audio_data.get('id')}, format={audio_data.get('format')}, bytes={len(audio_bytes)}, seconds={audio_seconds}"
                    )
                    audio_format = str(audio_data.get("format", "wav")).lower()
                    stt_result = blaze_stt(audio_bytes, audio_format=audio_format)
                    stt_engine = "Blaze"
                    st.session_state.logs.append(f"🔬 Blaze raw: {str(stt_result)[:300]}")
                    # Extract transcription from Blaze response dict
                    if isinstance(stt_result, dict):
                        result_obj = stt_result.get("result")
                        inner = None
                        source = "unknown"

                        # Blaze often nests transcription at result.data.*
                        if isinstance(result_obj, dict) and isinstance(result_obj.get("data"), dict):
                            inner = result_obj.get("data")
                            source = "result.data"
                        elif isinstance(stt_result.get("data"), dict):
                            inner = stt_result.get("data")
                            source = "data"
                        elif isinstance(result_obj, dict):
                            inner = result_obj
                            source = "result"
                        elif isinstance(stt_result.get("data"), str):
                            inner = stt_result.get("data")
                            source = "data(string)"
                        else:
                            inner = {}

                        st.session_state.logs.append(
                            f"🔑 parse from={source}, data type={type(inner).__name__}, keys={list(inner.keys()) if isinstance(inner, dict) else 'N/A'}"
                        )

                        transcription = ""
                        # Case 1: data is a dict — try all common transcription keys
                        if isinstance(inner, dict):
                            for key in ("transcription", "raw_text", "text", "result", "transcript", "content"):
                                val = inner.get(key)
                                if val and isinstance(val, str) and val.strip():
                                    transcription = val.strip()
                                    break
                        # Case 2: data is a plain string (some APIs return text directly)
                        elif isinstance(inner, str) and inner.strip():
                            transcription = inner.strip()

                        # Fallback: check top-level and nested result keys too
                        if not transcription:
                            for key in ("transcription", "text", "result", "transcript"):
                                val = stt_result.get(key)
                                if val and isinstance(val, str) and val.strip():
                                    transcription = val.strip()
                                    break
                        if not transcription and isinstance(result_obj, dict):
                            for key in ("transcription", "raw_text", "text", "transcript", "content"):
                                val = result_obj.get(key)
                                if val and isinstance(val, str) and val.strip():
                                    transcription = val.strip()
                                    break

                        if transcription:
                            # If transcript is too short for a long audio clip, try fallback STT.
                            words = len(transcription.split())
                            likely_truncated = bool(audio_seconds and audio_seconds >= 3.0 and words <= 1)
                            if likely_truncated:
                                st.session_state.logs.append(
                                    f"⚠️ Blaze transcript may be truncated (words={words}, seconds={audio_seconds}), trying fallback..."
                                )
                                fb = openai_stt(audio_bytes, audio_format=audio_format)
                                fb_text = fb.get("text") if isinstance(fb, dict) else None
                                if fb_text:
                                    voice_text = fb_text
                                    stt_engine = f"OpenAI ({fb.get('model', 'unknown')})"
                                    st.session_state.logs.append(
                                        f"🛟 Fallback STT ({fb.get('model', 'unknown')}): {voice_text[:60]}"
                                    )
                                else:
                                    voice_text = transcription
                                    st.session_state.logs.append(
                                        f"ℹ️ Fallback STT unavailable: {(fb.get('error') if isinstance(fb, dict) else 'unknown error')}"
                                    )
                            else:
                                voice_text = transcription
                        else:
                            fb = openai_stt(audio_bytes, audio_format=audio_format)
                            fb_text = fb.get("text") if isinstance(fb, dict) else None
                            if fb_text:
                                voice_text = fb_text
                                stt_engine = f"OpenAI ({fb.get('model', 'unknown')})"
                                st.session_state.logs.append(
                                    f"🛟 Fallback STT ({fb.get('model', 'unknown')}): {voice_text[:60]}"
                                )
                            else:
                                err = (
                                    stt_result.get("error")
                                    or (inner.get("error_message") if isinstance(inner, dict) else None)
                                    or (result_obj.get("error") if isinstance(result_obj, dict) else None)
                                    or "Không có nội dung"
                                )
                                st.session_state.logs.append(f"⚠️ STT no transcription found. Full response keys: {list(stt_result.keys())}")
                                st.toast(f"Không nhận dạng được: {err}", icon="⚠️")
                                voice_text = None
                    else:
                        voice_text = None

                    if voice_text:
                        st.session_state.last_stt_engine = stt_engine
                        st.session_state.logs.append(f"🎙️ STT ({stt_engine}): {voice_text[:60]}")
    except ImportError:
        pass

input_query = st.chat_input("Hỏi về tiệm rửa xe (VD: 'Tiệm nào an toàn nhất ở Tân Bình?')") or pending or voice_text

if input_query:
    # Ẩn panel chi tiết khi đang xử lý câu hỏi mới
    st.session_state.selected_shop = None
    st.session_state.messages.append({"role": "user", "content": input_query})
    with st.chat_message("user"):
        st.write(input_query)

    with st.spinner("🧠 Đang xử lý qua 3 agent..."):
        display_text, analyzed_shops, intent_info = run_pipeline(input_query)
        st.session_state.last_intent = intent_info.get("intent")

    if analyzed_shops:
        st.session_state.recommendations = analyzed_shops

    st.session_state.messages.append({"role": "assistant", "content": display_text})
    with st.chat_message("assistant"):
        st.markdown(display_text)

        # Voice output — TTS
        if st.session_state.voice_enabled and display_text:
            # Strip markdown for cleaner TTS
            clean_text = display_text.replace("**", "").replace("##", "").replace("- ", "")
            tts_text = clean_text[:500]  # Limit length for TTS
            with st.spinner("🔊 Đang tạo giọng nói..."):
                audio_bytes = blaze_tts(tts_text)
            if audio_bytes:
                st.audio(audio_bytes, format="audio/mp3", autoplay=True)

# ─────────────────────────────────────────────────────────────
#  SHOP LIST + DETAIL VIEW
# ─────────────────────────────────────────────────────────────
if st.session_state.recommendations:
    st.divider()

    # --- COLLAPSIBLE SHOP LIST ---
    shops = st.session_state.recommendations
    with st.expander(f"🏠 Kết quả đánh giá — {len(shops)} tiệm", expanded=True):
        for idx, shop in enumerate(shops[:10]):
            name = shop.get("name", "N/A")
            m = shop.get("metrics", {})
            trust = shop.get("_trust") or SafeWashEvaluator.calculate_trust_index(m)
            risk = shop.get("_risk") or SafeWashEvaluator.risk_label(m)

            # Risk color
            if risk == "CLOSED":
                badge = "🚫 Đã đóng"
            elif risk == "HIGH RISK":
                badge = f"🟥 {trust}/10"
            elif risk == "CAUTION":
                badge = f"🟧 {trust}/10"
            else:
                badge = f"🟩 {trust}/10"

            col_name, col_badge, col_btn = st.columns([5, 1.5, 1.5])
            col_name.markdown(f"**{name}**")
            col_badge.markdown(badge)
            if col_btn.button("🔍 Chi tiết", key=f"detail_{idx}", use_container_width=True):
                st.session_state.selected_shop = idx

    # --- DETAIL VIEW ---
    sel = st.session_state.selected_shop
    if sel is not None and sel < len(shops):
        shop = shops[sel]
        name = shop.get("name", "N/A")
        m = shop.get("metrics", {})
        trust = shop.get("_trust") or SafeWashEvaluator.calculate_trust_index(m)
        risk = shop.get("_risk") or SafeWashEvaluator.risk_label(m)
        info = shop.get("additional_info", {})

        st.divider()

        # Header row: name + close button
        hdr_col, close_col = st.columns([8, 1])
        hdr_col.subheader(f"🔍 {name}")
        if close_col.button("✖ Đóng", key="close_detail"):
            st.session_state.selected_shop = None
            st.rerun()

        # Trust badge
        if risk == "CLOSED":
            st.error("🚫 Đã đóng cửa vĩnh viễn")
        elif risk == "HIGH RISK":
            st.markdown(f'<div style="background:#ff4b4b;color:white;padding:10px 16px;border-radius:8px;text-align:center;font-size:1.2em;font-weight:bold;">⚠️ Nguy hiểm — Tin cậy: {trust}/10</div>', unsafe_allow_html=True)
        elif risk == "CAUTION":
            st.markdown(f'<div style="background:#ffa726;color:white;padding:10px 16px;border-radius:8px;text-align:center;font-size:1.2em;font-weight:bold;">⚡ Cẩn thận — Tin cậy: {trust}/10</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div style="background:#4caf50;color:white;padding:10px 16px;border-radius:8px;text-align:center;font-size:1.2em;font-weight:bold;">✅ An toàn — Tin cậy: {trust}/10</div>', unsafe_allow_html=True)

        st.write("")

        # --- Info columns ---
        left, right = st.columns(2)

        with left:
            st.markdown("#### 📍 Liên hệ & Vị trí")
            addr = info.get("address", "")
            phone = shop.get("phone", "")
            website = shop.get("website")
            if phone:
                st.markdown(f"📞 **SĐT:** {phone}")
            if addr:
                st.markdown(f"📍 **Địa chỉ:** {addr}")
            if website:
                st.markdown(f"🌐 **Website:** [{website}]({website})")

            # Google Maps directions link (uses current position)
            lat = shop.get("latitude")
            lng = shop.get("longitude")
            if lat and lng:
                maps_url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"
                st.markdown(f'[🗺️ Mở Google Maps (chỉ đường từ vị trí của bạn)]({maps_url})')

            # Services
            services = info.get("services", [])
            if services:
                st.markdown("#### 🛠️ Dịch vụ")
                for svc in services:
                    st.markdown(f"- {svc}")

        with right:
            st.markdown("#### 📊 Chi tiết Chỉ số")
            metric_labels = {
                "clean": "🧹 Sạch sẽ",
                "safe": "🛡️ An toàn",
                "support": "💬 Hỗ trợ",
                "speed": "⚡ Tốc độ",
                "price": "💰 Giá cả",
            }
            for key, label in metric_labels.items():
                val = m.get(key, 0)
                # Map -2..+2 to a visual bar (0-100%)
                pct = int((val + 2) / 4 * 100)
                if val > 0:
                    color = "#4caf50"
                elif val < 0:
                    color = "#ff4b4b"
                else:
                    color = "#9e9e9e"
                st.markdown(f"{label} ({val:+d})")
                st.markdown(f'<div style="background:#e0e0e0;border-radius:4px;height:8px;"><div style="background:{color};width:{pct}%;height:8px;border-radius:4px;"></div></div>', unsafe_allow_html=True)
                st.write("")

            # Booleans
            if m.get("multi_service"):
                st.markdown("✅ Đa dịch vụ")
            if m.get("is_franchise"):
                st.markdown("🏢 Chuỗi nhượng quyền")

            # Working hours
            hours = shop.get("working_hours", {})
            if hours:
                st.markdown("#### 🕔 Giờ hoạt động")
                for day, time in hours.items():
                    st.markdown(f"**{day}:** {time}")

        # --- Reviews (full width) ---
        reviews = shop.get("top_reviews", [])
        if reviews:
            st.markdown("#### 💬 Đánh giá")
            for r in reviews:
                rating = r.get("rating", "")
                text = r.get("text", "")
                date = r.get("date", "")
                owner_resp = r.get("owner_response", "")
                header = ""
                if rating:
                    header += f"⭐ {rating}"
                if date:
                    header += f" · {date}"
                if header:
                    st.markdown(f"**{header}**")
                st.markdown(f"> {text}")
                if owner_resp:
                    st.markdown(f"💬 **Phản hồi chủ tiệm:** {owner_resp}")
                images = r.get("images", [])
                if images:
                    img_cols = st.columns(min(3, len(images)))
                    for img_i, img_url in enumerate(images[:3]):
                        img_cols[img_i].image(img_url, width=200)
                st.divider()
