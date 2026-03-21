import streamlit as st
import os
import asyncio
import json
import sys
import nest_asyncio
import re
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# MCP & Core
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from core import SYSTEM_PROMPT, SafeWashEvaluator
# Tạm thời comment voice engine để tránh lỗi import nếu chưa fix xong env
# from core import elevenlabs_stt, elevenlabs_tts 

# --- SETTINGS ---
VOICE_ENABLED = False  # Gạt sang True nếu ông muốn dùng lại Voice
load_dotenv()
nest_asyncio.apply()

# --- CONFIG ---
SERVER_SCRIPT = Path(__file__).parent / "server" / "mcp_server.py"
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(SERVER_SCRIPT)],
    env=os.environ.copy()
)

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))

st.set_page_config(page_title="SafeWash AI Auditor", page_icon="🛡️", layout="wide")

# Session State initialization
if "messages" not in st.session_state: st.session_state.messages = []
if "recommendations" not in st.session_state: st.session_state.recommendations = []
if "logs" not in st.session_state: st.session_state.logs = []

# --- AGENT LOGIC: LLM-as-a-Judge ---
async def call_agent_with_mcp(user_input):
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                st.session_state.logs.append("✅ MCP Server connected.")

                # BƯỚC 1: Lấy dữ liệu thô từ MCP Tool
                data_response = await session.call_tool("get_all_stores", {})

                # Robust extract text payload (tùy MCP content shape)
                raw_data = ""
                if getattr(data_response, "content", None):
                    c0 = data_response.content[0]
                    raw_data = getattr(c0, "text", "") or getattr(c0, "data", "") or ""
                if not raw_data:
                    raw_data = "[]"

                st.session_state.logs.append(f"📦 Evidence gathered: {len(raw_data)} bytes")

                # BƯỚC 2: Gọi LLM để Judge
                user_payload = f"DATA TO AUDIT:\n{raw_data}\n\nUSER REQUEST: {user_input}"

                response = client.chat.completions.create(
                    model="openai/gpt-4o",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        # (optional) lịch sử chat nếu bạn thật sự cần:
                        # ...st.session_state.messages,
                        {"role": "user", "content": user_payload},
                    ],
                    response_format={"type": "json_object"},
                )
                return response.choices[0].message.content, raw_data
    except Exception as e:
        st.session_state.logs.append(f"❌ MCP/LLM Error: {str(e)}")
        return json.dumps({"comment": "Lỗi kết nối dữ liệu.", "scores": None}), "[]"

# --- UI HEADER ---
st.title("🛡️ SafeWash AI: Expert Auditor")
st.caption(f"Trạng thái: {'Voice Active 🎤' if VOICE_ENABLED else 'Text Only 📝'}")

with st.expander("🛠️ Debug Trace System"):
    for log in st.session_state.logs:
        st.caption(log)

# --- RENDER CHAT ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- INPUT HANDLING ---
st.write("---")

# Logic Voice (Đã comment lại)
# if VOICE_ENABLED:
#     from streamlit_mic_recorder import mic_recorder
#     audio_data = mic_recorder(start_prompt="🎤 Nói chuyện", stop_prompt="🛑 Dừng", key="v_input")
#     if audio_data:
#         input_query = elevenlabs_stt(audio_data['bytes'])
#         if input_query: st.chat_message("user").write(input_query)

input_query = st.chat_input("Hỏi về độ an toàn (VD: 'Quận 7 tiệm nào rửa kỹ không hại sơn?')")

if input_query:
    st.session_state.messages.append({"role": "user", "content": input_query})
    with st.chat_message("user"): st.write(input_query)
    
    with st.spinner("🕵️ AI đang thực hiện thẩm định chuyên sâu..."):
        ai_reply_json_str, raw_stores = asyncio.run(call_agent_with_mcp(input_query))
        
        try:
            audit_data = json.loads(ai_reply_json_str)
            # Tính điểm Overall bằng Evaluator (Toán học dựa trên AI Scores)
            if "scores" in audit_data and audit_data["scores"]:
                overall_score = SafeWashEvaluator.calculate_trust_index(audit_data['scores'])
                display_reply = f"{audit_data.get('comment', '')}\n\n📊 **SafeWash Index: {overall_score}/10**"
            else:
                display_reply = audit_data.get('comment', "AI không trả về điểm số thẩm định.")
        except:
            display_reply = ai_reply_json_str

        st.session_state.messages.append({"role": "assistant", "content": display_reply})
        with st.chat_message("assistant"):
            st.markdown(display_reply)
            
            # Logic TTS (Đã comment lại)
            # if VOICE_ENABLED:
            #     audio_bytes = elevenlabs_tts(display_reply)
            #     if audio_bytes: st.audio(audio_bytes, format="audio/mp3", autoplay=True)

    # st.rerun() # Để lại rerun nếu cần UI update tức thì

# --- CARDS: SHOW EVIDENCE ---
if st.session_state.recommendations:
    st.divider()
    cols = st.columns(min(3, len(st.session_state.recommendations)))
    for idx, shop in enumerate(st.session_state.recommendations[:3]):
        with cols[idx]:
            with st.container(border=True):
                st.markdown(f"### {shop['name']}")
                m = shop['metrics']
                if m['safe'] < 0: st.error("⚠️ HIGH RISK")
                else: st.success("✅ SafeWash Standard")
                st.caption(f"📞 {shop['phone']}")