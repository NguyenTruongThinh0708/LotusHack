import streamlit as st
import os
import asyncio
import json
from dotenv import load_dotenv
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder

# MCP Imports
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Core Imports (Rút gọn nhờ __init__.py)
from core import elevenlabs_stt, elevenlabs_tts, SYSTEM_PROMPT
import streamlit as st
import asyncio
import nest_asyncio

nest_asyncio.apply() # Cho phép chạy asyncio.run() trong Streamlit

load_dotenv()

# --- CẤU HÌNH MCP SERVER ---
# Lệnh này tự động chạy "python server/mcp_server.py"
server_params = StdioServerParameters(
    command="python",
    args=["server/mcp_server.py"],
    env=os.environ.copy()
)

# --- KHỞI TẠO UI ---
st.set_page_config(page_title="SafeWash AI", page_icon="🚗", layout="wide")
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))

if "messages" not in st.session_state:
    st.session_state.messages = []
if "recommendations" not in st.session_state:
    st.session_state.recommendations = []

# --- HÀM XỬ LÝ AGENT (ASYNC) ---
async def call_agent_with_mcp(user_input):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Bắt tay với MCP Server
            await session.initialize()
            
            # 2. Gửi request lên OpenRouter kèm định nghĩa Tool từ MCP
            # Lưu ý: Ở bản Hackathon nhanh, ta có thể gọi Tool trước để lấy Data 
            # rồi mới ném vào LLM (RAG-style) hoặc dùng Tool Call thực thụ.
            
            # Giả lập: Lấy danh sách tiệm từ MCP Tool
            data_response = await session.call_tool("get_all_stores", {})
            raw_data = data_response.content[0].text
            
            # 3. LLM Reasoning
            full_prompt = f"{SYSTEM_PROMPT}\n\nDATA CONTEXT: {raw_data}"
            response = client.chat.completions.create(
                model="openai/gpt-4o",
                messages=[
                    {"role": "system", "content": full_prompt},
                    *st.session_state.messages
                ]
            )
            return response.choices[0].message.content, raw_data

# --- GIAO DIỆN CHÍNH ---
st.title("🚗 SafeWash AI Assistant")

# Hiển thị Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

st.write("---")
audio_data = mic_recorder(start_prompt="🎤 Nói với trợ lý", stop_prompt="🛑 Kết thúc", key="voice_input")

user_text = st.chat_input("Nhập yêu cầu...")
input_query = None

if audio_data:
    with st.spinner("Đang nghe..."):
        input_query = elevenlabs_stt(audio_data['bytes'])
if user_text:
    input_query = user_text

if input_query:
    st.session_state.messages.append({"role": "user", "content": input_query})
    with st.chat_message("user"): st.write(input_query)

    with st.spinner("AI đang thẩm định dữ liệu..."):
        # Chạy Async trong môi trường Sync của Streamlit
        ai_reply, raw_stores = asyncio.run(call_agent_with_mcp(input_query))
        
        # Cập nhật Recommendations vào session state (Giả lập parse từ LLM)
        st.session_state.recommendations = json.loads(raw_stores)[:3] # Lấy 3 cái đầu
        
        st.session_state.messages.append({"role": "assistant", "content": ai_reply})
        with st.chat_message("assistant"):
            st.markdown(ai_reply)
            
            # TTS
            audio_bytes = elevenlabs_tts(ai_reply)
            st.audio(audio_bytes, format="audio/mp3", autoplay=True)
    st.rerun()

# --- HIỂN THỊ CARDS (TOP-K) ---
if st.session_state.recommendations:
    st.write("### 📍 Tiệm đề xuất dựa trên phân tích rủi ro")
    cols = st.columns(len(st.session_state.recommendations))
    for idx, shop in enumerate(st.session_state.recommendations):
        with cols[idx]:
            with st.container(border=True):
                # Tính điểm nhanh hiển thị (Logic từ evaluator.py)
                clean_score = shop['metrics']['clean']
                st.subheader(shop['name'])
                st.write(f"🛡️ Safety Index: **{clean_score}/5**")
                if shop['metrics']['safe'] < 0:
                    st.error("⚠️ Cảnh báo rủi ro")
                else:
                    st.success("✅ Đạt chuẩn SafeWash")
                st.caption(f"📞 {shop['phone']}")