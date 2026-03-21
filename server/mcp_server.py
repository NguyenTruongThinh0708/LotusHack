# server/mcp_server.py
from mcp.server.fastmcp import FastMCP
import json
import os

mcp = FastMCP("SafeWash-Data-Bridge")

@mcp.tool()
def get_shop_raw_data(shop_id: str) -> str:
    """Lấy toàn bộ dữ liệu thô của tiệm để LLM bắt đầu chấm điểm."""
    with open("data/stores.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        for s in data:
            if s['id'] == shop_id:
                return json.dumps(s, ensure_ascii=False)
    return "Not Found"